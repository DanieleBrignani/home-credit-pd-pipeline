# Runbook

Local-development operations. Nothing here has been exercised against production traffic.

## Create an API key

```bash
python -c "import secrets,hashlib;k=secrets.token_urlsafe(24);print('KEY (give to client, never store):',k);print('API_KEY_SHA256S=',hashlib.sha256(k.encode()).hexdigest())"
```

Put the hash (comma-separate several for rotation) in `.env` as `API_KEY_SHA256S`. The API refuses to start without it.
`/predict` and `/model` require `X-API-Key`; `/health`, `/ready`, `/metrics` do not (restrict `/metrics` at the network layer).

## Promote a model

```bash
python -m creditrisk.registry status
python -m creditrisk.registry promote <version> --dry-run     # shows every gate
python -m creditrisk.registry promote <version>
curl -X POST localhost:8000/admin/reload -H "x-admin-token: $ADMIN_TOKEN"
```

A rejected promotion is logged to `reports/model_decisions.jsonl` with the failing gate.

## Roll back a model

```bash
python -m creditrisk.registry rollback --reason "PSI alarm on ext_source_mean"   # champion := previous_champion
# or an explicit target:  --to <version>
curl -X POST localhost:8000/admin/reload -H "x-admin-token: $ADMIN_TOKEN"
curl localhost:8000/ready                                                          # confirm model_version
```

Rollback only works for versions whose `feature_schema_version` still matches what the API builds; after a feature change, roll back the code too.
In-flight requests finish on the old model; there is no partial state. Time to roll back is not measured against real incidents.

## A data file changed / contract violation

`creditrisk.ingest` exits with code 2 and loads nothing. Do **not** edit `MANIFEST.sha256` to silence it: confirm with the data owner, then
re-download the original, or move the new file to a new name and add a new contract version.

## Backup and restore

```bash
python scripts/backup_restore.py backup
python scripts/backup_restore.py drill      # backup -> restore into a scratch DB -> compares row counts of audit/meta/marts
```

Derived schemas (staging, intermediate, marts) are rebuilt from raw with `dbt build`; the **audit trail and MLflow registry are not reproducible** and
are what the backup protects. Backups here are local files — copy them off the machine. Retention and encryption are not implemented.

## Audit store down

The API returns `503` and releases no score (fail-closed). Restore the database, then `/ready` and `/predict` recover without restart.

## Secrets

`.env` is git-ignored but, if the project folder is under a synced drive (e.g. OneDrive), it is synced. Keep real secrets out of synced folders; the values in `.env.example` are placeholders.
