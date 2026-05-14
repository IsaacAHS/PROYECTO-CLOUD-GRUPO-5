import json
import os
from pathlib import Path
from typing import Any


JOB_DIR = Path(os.getenv("NIMBUSCORE_JOB_DIR", "/tmp/nimbuscore_jobs"))


def write_job(job: dict[str, Any]) -> Path:
    JOB_DIR.mkdir(parents=True, exist_ok=True)
    path = JOB_DIR / f"{job['id']}.json"
    path.write_text(json.dumps(job, indent=2), encoding="utf-8")
    return path


def read_job(job_id: str) -> dict[str, Any] | None:
    path = JOB_DIR / f"{job_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
