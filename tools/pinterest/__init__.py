"""Pinterest pin generation pipeline (tools/pins.py is the CLI entry).

Reuses the repo's catalog loader (common.py), R2 client (publish.py) and
Haar-cascade face detection (ingest_quality.py); adds nothing that duplicates
them. Package layout:

  config.py      the five config/pinterest_*.yaml files
  catalog.py     active poses + unique verbal prompts via common.load_pose
  provenance.py  pose id -> shoot / source filename, from inbox drafts
  rights.py      the rights gate (absolute exclusion, fails loudly)
  metadata.py    deterministic title / description / keywords / link / alt
  text_fit.py    typography auto-fit (never overflows, never orphans)
  render.py      text pins, photo pins, contact sheet
  schedule.py    ramped publish schedule with jittered unique timestamps
  manifest.py    state/pinterest_manifest.json
  selection.py   round-robin, share-respecting candidate selection
  upload.py      content-hashed idempotent upload under pins/
  csv_out.py     batched, interleaved, schema-driven CSVs
  commands.py    generate / upload / csv / status / scan-rights
"""
from __future__ import annotations

import sys
from pathlib import Path

# Sibling tools (common.py, publish.py, ingest_quality.py) are plain modules
# in tools/, imported the way every other tool imports them.
TOOLS_DIR = Path(__file__).resolve().parent.parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))
