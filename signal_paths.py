"""Configurable local paths for signal inputs, state, and immutable outputs."""

from __future__ import annotations

import os
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parent
MARKET_DATA_ROOT = Path(
    os.environ.get("MARKET_DATA_ROOT", REPOSITORY_ROOT / "data")
).expanduser().resolve()
SIGNAL_STATE_ROOT = Path(
    os.environ.get("SIGNAL_STATE_ROOT", REPOSITORY_ROOT / "state")
).expanduser().resolve()
SIGNAL_ARTIFACT_ROOT = Path(
    os.environ.get("SIGNAL_ARTIFACT_ROOT", REPOSITORY_ROOT / "artifacts")
).expanduser().resolve()
CLASSIFICATION_PATH = Path(
    os.environ.get("MARKET_CLASSIFICATION_PATH", MARKET_DATA_ROOT / "classification.csv")
).expanduser().resolve()
