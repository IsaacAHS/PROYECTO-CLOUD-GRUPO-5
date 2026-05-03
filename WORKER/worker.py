import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from script_runner import run_script_command
from topology_mapper import script_commands_for_job


JOB_DIR = Path(os.getenv("NIMBUSCORE_JOB_DIR", "/jobs"))
RUN_DIR = Path(os.getenv("NIMBUSCORE_RUN_DIR", "/script-runs"))
POLL_SECONDS = int(os.getenv("NIMBUSCORE_WORKER_POLL_SECONDS", "3"))


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def log(message: str) -> None:
    print(f"[{now_iso()}] {message}", flush=True)


def process_script_job(path: Path, job: dict) -> None:
    job_run_dir = RUN_DIR / job["id"]
    job_run_dir.mkdir(parents=True, exist_ok=True)

    commands = script_commands_for_job(job)
    job["script_runner"] = {
        "mode": "scripts",
        "commands": [" ".join(command) for command in commands],
    }
    log(f"job={job['id']} comandos preparados: {job['script_runner']['commands']}")

    logs = []
    for command in commands:
        log(f"job={job['id']} ejecutando: {' '.join(command)}")
        command_log = run_script_command(command, job_run_dir)
        logs.append(command_log)
        if command_log["exit_code"] != 0:
            job["status"] = "FAILED"
            job["message"] = f"Fallo ejecutando script: {command_log['command']}"
            job["logs"] = logs
            job["updated_at"] = now_iso()
            write_json(path, job)
            log(f"job={job['id']} FAILED: {job['message']}")
            if command_log.get("output"):
                log(f"job={job['id']} output:\n{command_log['output']}")
            return

    job["status"] = "SUCCESS"
    job["message"] = "Scripts de topologia procesados correctamente."
    job["logs"] = logs
    job["updated_at"] = now_iso()
    write_json(path, job)
    log(f"job={job['id']} SUCCESS")


def process_job(path: Path) -> None:
    job = json.loads(path.read_text(encoding="utf-8"))
    if job.get("status") != "QUEUED":
        return

    log(f"job={job['id']} recibido desde {path}")
    job["status"] = "RUNNING"
    job["message"] = "Worker procesando job en modo scripts."
    job["updated_at"] = now_iso()
    write_json(path, job)

    process_script_job(path, job)


def main() -> None:
    JOB_DIR.mkdir(parents=True, exist_ok=True)
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    log(f"worker iniciado mode=scripts job_dir={JOB_DIR} run_dir={RUN_DIR}")

    while True:
        for path in sorted(JOB_DIR.glob("job-*.json")):
            try:
                process_job(path)
            except Exception as exc:
                job = json.loads(path.read_text(encoding="utf-8"))
                job["status"] = "FAILED"
                job["message"] = f"Error del worker: {exc}"
                job["updated_at"] = now_iso()
                write_json(path, job)
                log(f"job={job.get('id', path.name)} FAILED exception={exc}")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
