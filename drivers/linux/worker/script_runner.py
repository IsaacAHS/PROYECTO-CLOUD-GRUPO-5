import os
import re
import subprocess
from pathlib import Path


DRY_RUN = os.getenv("NIMBUSCORE_SCRIPT_DRY_RUN", "true").lower() == "true"
SECRET_ENV_RE = re.compile(r"(NIMBUSCORE_CONSOLE_PASSWORD=)(?:'[^']*'|\"[^\"]*\"|\S+)")


def format_command(command: list[str]) -> str:
    command_text = " ".join(command)
    return SECRET_ENV_RE.sub(r"\1'***'", command_text)


def run_script_command(command: list[str], cwd: Path) -> dict:
    command_text = format_command(command)
    if DRY_RUN:
        return {
            "command": command_text,
            "exit_code": 0,
            "output": "DRY_RUN=true: comando preparado, no ejecutado.",
        }

    result = subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)
    return {
        "command": command_text,
        "exit_code": result.returncode,
        "output": (result.stdout + result.stderr)[-8000:],
    }
