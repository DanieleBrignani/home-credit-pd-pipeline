"""Champion / challenger lifecycle on the MLflow Model Registry (aliases: champion, challenger, previous_champion).

PROMOTION RULE (evaluated on the VALIDATION split only; the test split is never used for decisions):
  gate 0  both versions were trained on canonical Kaggle files          (tag canonical_kaggle_files == True)
  gate 1  challenger.feature_schema_version == current feature_schema_version()   (serving compatibility)
  gate 2  challenger valid ROC-AUC  >= champion valid ROC-AUC + MIN_AUC_GAIN
  gate 3  challenger valid Brier    <= champion valid Brier * (1 + MAX_BRIER_REGRESSION)
  gate 4  challenger valid KS       >= champion valid KS - MAX_KS_DROP
  bootstrap (no champion yet): sanity gate only -> valid ROC-AUC >= MIN_BOOTSTRAP_AUC and |calibration_slope-1| <= 0.3
Every decision (promote / reject / rollback) is appended to reports/model_decisions.jsonl and stored as a tag on the
model version, so the history is auditable. Rollback = repoint the `champion` alias; the API re-resolves the alias.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

import mlflow
from mlflow.tracking import MlflowClient

from . import config
from .features import feature_schema_version
from .train import REGISTERED_MODEL, tracking_uri

MIN_AUC_GAIN = 0.002
MAX_BRIER_REGRESSION = 0.02
MAX_KS_DROP = 0.005
MIN_BOOTSTRAP_AUC = 0.60
DECISION_LOG = config.REPORT_DIR / "model_decisions.jsonl"


def _client() -> MlflowClient:
    mlflow.set_tracking_uri(tracking_uri())
    return MlflowClient()


def _alias_version(c: MlflowClient, alias: str):
    try:
        return c.get_model_version_by_alias(REGISTERED_MODEL, alias)
    except Exception:
        return None


def _run_metrics(c: MlflowClient, mv) -> tuple[dict, dict]:
    run = c.get_run(mv.run_id)
    return run.data.metrics, run.data.tags


def evaluate_promotion(c: MlflowClient, challenger_version: int) -> dict:
    ch = c.get_model_version(REGISTERED_MODEL, str(challenger_version))
    cm, ct = _run_metrics(c, ch)
    champ = _alias_version(c, "champion")
    checks: list[dict] = []

    def chk(name, ok, detail):
        checks.append({"gate": name, "passed": bool(ok), "detail": detail})

    chk("gate0_canonical_data", ct.get("canonical_kaggle_files") == "True", f"tag={ct.get('canonical_kaggle_files')}")
    chk("gate1_feature_schema", ct.get("feature_schema_version") == feature_schema_version(),
        f"model={ct.get('feature_schema_version')} current={feature_schema_version()}")
    if champ is None:
        slope = cm.get("valid_calibration_slope", 0)
        chk("bootstrap_min_auc", cm.get("valid_roc_auc", 0) >= MIN_BOOTSTRAP_AUC, f"auc={cm.get('valid_roc_auc')}")
        chk("bootstrap_calibration", abs(slope - 1) <= 0.3, f"slope={slope}")
    else:
        km, _ = _run_metrics(c, champ)
        chk("gate2_auc_gain", cm["valid_roc_auc"] >= km["valid_roc_auc"] + MIN_AUC_GAIN,
            f"challenger={cm['valid_roc_auc']:.4f} champion={km['valid_roc_auc']:.4f} need +{MIN_AUC_GAIN}")
        chk("gate3_brier", cm["valid_brier"] <= km["valid_brier"] * (1 + MAX_BRIER_REGRESSION),
            f"challenger={cm['valid_brier']:.5f} champion={km['valid_brier']:.5f}")
        chk("gate4_ks", cm["valid_ks"] >= km["valid_ks"] - MAX_KS_DROP,
            f"challenger={cm['valid_ks']:.4f} champion={km['valid_ks']:.4f}")
    return {"challenger_version": int(challenger_version),
            "champion_version": int(champ.version) if champ else None,
            "promote": all(x["passed"] for x in checks), "checks": checks}


def _log_decision(c: MlflowClient, kind: str, version: int, payload: dict):
    rec = {"ts": datetime.now(timezone.utc).isoformat(), "kind": kind, "version": version,
           "git_commit": config.git_commit(), **payload}
    DECISION_LOG.parent.mkdir(exist_ok=True, parents=True)
    with open(DECISION_LOG, "a") as f:
        f.write(json.dumps(rec) + "\n")
    c.set_model_version_tag(REGISTERED_MODEL, str(version), f"decision_{kind}", rec["ts"])


def set_challenger(version: int) -> None:
    c = _client()
    c.set_registered_model_alias(REGISTERED_MODEL, "challenger", str(version))


def promote(version: int, dry_run: bool = False, force_reason: str | None = None) -> dict:
    """force_reason: operator override. The gates are still evaluated and recorded, the failing ones are listed in the decision,
    and the override is logged as `override_promoted` with the stated reason, so it stays auditable."""
    c = _client()
    decision = evaluate_promotion(c, version)
    if force_reason and not dry_run:
        if not decision["promote"]:
            decision = {**decision, "overridden_failed_gates": [x["gate"] for x in decision["checks"] if not x["passed"]],
                        "promote": True, "override_reason": force_reason}
            kind = "override_promoted"
        else:
            kind = "promoted"
        old = _alias_version(c, "champion")
        if old is not None:
            c.set_registered_model_alias(REGISTERED_MODEL, "previous_champion", old.version)
        c.set_registered_model_alias(REGISTERED_MODEL, "champion", str(version))
        _log_decision(c, kind, version, decision)
        return decision
    if dry_run or not decision["promote"]:
        if not dry_run:
            _log_decision(c, "rejected", version, decision)
        return decision
    old = _alias_version(c, "champion")
    if old is not None:
        c.set_registered_model_alias(REGISTERED_MODEL, "previous_champion", old.version)
    c.set_registered_model_alias(REGISTERED_MODEL, "champion", str(version))
    try:
        c.delete_registered_model_alias(REGISTERED_MODEL, "challenger")
    except Exception:
        pass
    _log_decision(c, "promoted", version, decision)
    return decision


def rollback(to_version: int | None = None, reason: str = "manual") -> dict:
    c = _client()
    cur = _alias_version(c, "champion")
    target = c.get_model_version(REGISTERED_MODEL, str(to_version)) if to_version else _alias_version(c, "previous_champion")
    if target is None:
        raise RuntimeError("no rollback target: no previous_champion alias and no explicit --to")
    if cur is not None and cur.version == target.version:
        raise RuntimeError("rollback target equals current champion")
    if cur is not None:
        c.set_registered_model_alias(REGISTERED_MODEL, "previous_champion", cur.version)
    c.set_registered_model_alias(REGISTERED_MODEL, "champion", target.version)
    out = {"from": int(cur.version) if cur else None, "to": int(target.version), "reason": reason}
    _log_decision(c, "rollback", int(target.version), out)
    return out


def lifecycle_step(versions: dict[str, int], selected: str) -> list[dict]:
    """Policy used by the pipeline: with no champion, bootstrap with the simplest model (the logistic baseline, if it passes the
    sanity gates); the selected model then has to EARN promotion against it through the rule. Returns the decisions taken."""
    decisions = []
    c = _client()
    if _alias_version(c, "champion") is None:
        decisions.append({"step": "bootstrap_baseline", **promote(versions["logreg"])})
    cur = _alias_version(c, "champion")
    if selected != "logreg" and (cur is None or int(cur.version) != versions[selected]):
        set_challenger(versions[selected])
        decisions.append({"step": "challenger_vs_champion", **promote(versions[selected])})
    return decisions


def decision_history() -> list[dict]:
    return [json.loads(l) for l in DECISION_LOG.read_text().splitlines()] if DECISION_LOG.exists() else []


def status() -> dict:
    c = _client()
    out = {}
    for alias in ("champion", "challenger", "previous_champion"):
        mv = _alias_version(c, alias)
        out[alias] = None if mv is None else {"version": int(mv.version), "run_id": mv.run_id,
                                              "family": mv.tags.get("family")}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    p = sub.add_parser("promote"); p.add_argument("version", type=int); p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force", metavar="REASON", help="operator override with a mandatory reason; gates are still recorded")
    r = sub.add_parser("rollback"); r.add_argument("--to", type=int); r.add_argument("--reason", default="manual")
    ch = sub.add_parser("challenger"); ch.add_argument("version", type=int)
    a = ap.parse_args(argv)
    res = {"status": status, "promote": lambda: promote(a.version, a.dry_run, a.force), "challenger": lambda: set_challenger(a.version),
           "rollback": lambda: rollback(a.to, a.reason)}[a.cmd]
    print(json.dumps(res() if callable(res) else res, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
