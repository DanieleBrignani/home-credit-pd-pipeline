# Runbook

Local-development operations. Nothing here has been exercised against production traffic.

## Create an API key

```bash
python -c "import secrets,hashlib;k=secrets.token_urlsafe(24);print('KEY (give to client, never store):',k);print('API_KEY_SHA256S=',hashlib.sha256(k.encode()).hexdigest())"
```

Put the hash (comma-separate several for rotation) in `.env` as `API_KEY_SHA256S`. The API refuses to start without it.
`/predict` and `/model` require `X-API-Key`; `/health` and `/ready` do not. Each key is rate-limited (token bucket, `RATE_LIMIT_PER_MINUTE`, per API process:
with several workers the effective limit is per worker). Over the limit the API answers `429` with `Retry-After`.

## Network entry and TLS

Public traffic goes through the Caddy proxy (`https://localhost:${TLS_PORT:-8443}`) with a certificate from Caddy's local CA (development-grade; use a real host name and a
publicly trusted certificate, or your load balancer, for anything else). The proxy answers `404` for `/admin/*` and `/metrics`. The API's own port is bound to the host loopback only,
so operational calls are made from the host: `curl -X POST localhost:8000/admin/reload -H "x-admin-token: $ADMIN_TOKEN"`. Prometheus scrapes the API inside the compose network.

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
python scripts/backup_restore.py backup           # irreproducible data only (audit, MLflow registry, lineage, marts)
python scripts/backup_restore.py backup --full    # also raw/staging/intermediate (multi-gigabyte)
python scripts/backup_restore.py drill            # backup -> restore into a scratch DB -> compares table row counts
```

Derived schemas (staging, intermediate, marts) are rebuilt from raw with `dbt build`; the **audit trail and MLflow registry are not reproducible** and
are what the backup protects. Backups here are local files — copy them off the machine. Retention and encryption are not implemented.

## Audit store down

The API returns `503` and releases no score (fail-closed). Restore the database, then `/ready` and `/predict` recover without restart.

## Secrets

`.env` is git-ignored but, if the project folder is under a synced drive (e.g. OneDrive), it is synced. Keep real secrets out of synced folders; the values in `.env.example` are placeholders.
