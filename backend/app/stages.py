"""Analysis stages.

The strings and their ORDER are load-bearing: frontend Processing.tsx renders its
checklist with STAGES.indexOf(inv.progress.stage). Do not reorder or rename.
"""
from __future__ import annotations

STAGES: tuple[str, ...] = (
    "ingest",
    "frames",
    "faces",
    "spatial",
    "temporal",
    "frequency",
    "fusion",
    "done",
)

STAGE_PCT: dict[str, int] = {
    "ingest": 5,
    "frames": 20,
    "faces": 35,
    "spatial": 55,
    "temporal": 70,
    "frequency": 82,
    "fusion": 92,
    "done": 100,
}


def pct_for(stage: str) -> int:
    return STAGE_PCT.get(stage, 0)
