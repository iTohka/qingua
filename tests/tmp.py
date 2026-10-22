from __future__ import annotations

import gc
import shutil
import time
from pathlib import Path
from uuid import uuid4

PROJECT = Path(__file__).resolve().parents[1]


def workspace_temp(prefix: str) -> Path:
    path = PROJECT / "data" / f"{prefix}-{uuid4().hex[:10]}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def cleanup(path: Path) -> None:
    # SQLite on Windows can keep the file handle for a moment after close().
    for _ in range(30):
        gc.collect()
        try:
            shutil.rmtree(path)
            return
        except FileNotFoundError:
            return
        except PermissionError:
            time.sleep(0.1)
