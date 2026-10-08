"""Mechanical check that every number in README.md / RECRUITER_SUMMARY.md is reproducible by a repository command.

Rules
  1. The generated results blocks must equal what `scripts/make_results.py` renders from reports/*.json  (so every number
     in them traces to a JSON file written by a command listed in docs/reproducibility.md).
  2. Outside those blocks, prose may not contain bare numeric claims. Allowed: numbers inside `inline code` or fenced
     code (commands, ports, versions), list/heading ordinals, and tokens explicitly registered in docs/claims.json with a
     reproducing command.
  3. Every command referenced by a claim must exist in the repository (file/module exists).
Exit code 1 on violation. Run: python scripts/verify_readme_numbers.py
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BLOCKS = [(r"<!-- RESULTS:START.*?-->", r"<!-- RESULTS:END -->"), (r"<!-- MEASURED:START.*?-->", r"<!-- MEASURED:END -->")]
NUM = re.compile(r"(?<![\w.`/-])\d[\d,]*(?:\.\d+)?%?(?![\w-])")


def strip_generated(text: str) -> str:
    for s, e in BLOCKS:
        text = re.sub(s + r".*?" + e, "", text, flags=re.S)
    return text


def strip_code(text: str) -> str:
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    text = re.sub(r"`[^`]*`", "", text)
    text = re.sub(r"\]\([^)]*\)", "]", text)                    # link targets
    text = re.sub(r"^\s*(#+\s*)?\d+[.)]\s", "", text, flags=re.M)  # ordinals
    text = re.sub(r"^\s*\|[\s:|-]+\|\s*$", "", text, flags=re.M)  # table separators
    return text


def main() -> int:
    problems: list[str] = []
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "make_results.py"), "--check"], capture_output=True, text=True)
    if r.returncode != 0:
        problems.append("results blocks stale or reports missing: " + (r.stdout + r.stderr).strip()[-400:])
    claims = json.loads((ROOT / "docs" / "claims.json").read_text()) if (ROOT / "docs" / "claims.json").exists() else {}
    allowed = set(claims)
    for name in ("README.md", "RECRUITER_SUMMARY.md"):
        prose = strip_code(strip_generated((ROOT / name).read_text(encoding="utf-8")))
        for m in NUM.finditer(prose):
            tok = m.group(0)
            if tok not in allowed:
                ctx = prose[max(0, m.start() - 40): m.end() + 30].replace("\n", " ")
                problems.append(f"{name}: unregistered number '{tok}' ... {ctx} ...")
    for tok, c in claims.items():
        for key in ("command", "source"):
            if key not in c:
                problems.append(f"claims.json[{tok}] missing '{key}'")
        cmd = c.get("command", "")
        for path in re.findall(r"(?:scripts|src|dbt|tests)/[\w/.\-]+", cmd):
            if not (ROOT / path).exists():
                problems.append(f"claims.json[{tok}] references missing path {path}")
    print("\n".join(problems) if problems else "OK: every number in README.md / RECRUITER_SUMMARY.md is generated from reports/*.json "
                                              "or registered in docs/claims.json with an existing command")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
