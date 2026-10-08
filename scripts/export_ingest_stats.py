"""Export raw-load lineage/timing from meta.ingest_batches to reports/ingest_load.json (the real COPY time of each file,
not the near-zero time of an idempotent re-run)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from creditrisk import data  # noqa: E402

with data.connect() as c:
    cur = c.cursor()
    cur.execute("select table_name, row_count, load_seconds from meta.ingest_batches order by loaded_at")
    rows = cur.fetchall()
out = {"tables": {t: {"rows": int(n), "load_seconds": float(s)} for t, n, s in rows},
       "total_load_seconds": float(sum(s for _, _, s in rows))}
(ROOT / "reports" / "ingest_load.json").write_text(json.dumps(out, indent=2))
print(json.dumps(out, indent=2))
