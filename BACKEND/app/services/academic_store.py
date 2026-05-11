import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ACADEMIC_STORE_PATH = Path(os.getenv("NIMBUSCORE_ACADEMIC_STORE_PATH", "/data/academic.json"))


DEFAULT_COURSES = [
    {
        "id": "TEL141",
        "nombre": "Ingenieria de Redes Cloud",
        "alumnos": [
            {"id": "TEL141-A01", "nombre": "Ana Torres"},
            {"id": "TEL141-A02", "nombre": "Bruno Diaz"},
            {"id": "TEL141-A03", "nombre": "Carla Rojas"},
        ],
    },
    {
        "id": "TEL142",
        "nombre": "Comunicaciones Moviles",
        "alumnos": [
            {"id": "TEL142-A01", "nombre": "Diego Vargas"},
            {"id": "TEL142-A02", "nombre": "Elena Ramos"},
            {"id": "TEL142-A03", "nombre": "Fernando Silva"},
        ],
    },
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_academic_store(courses: list[dict[str, Any]]) -> None:
    ACADEMIC_STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    ACADEMIC_STORE_PATH.write_text(
        json.dumps(
            {
                "updated_at": now_iso(),
                "courses": courses,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def load_courses() -> list[dict[str, Any]]:
    ACADEMIC_STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not ACADEMIC_STORE_PATH.exists():
        write_academic_store(DEFAULT_COURSES)
        return DEFAULT_COURSES

    try:
        raw = json.loads(ACADEMIC_STORE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        write_academic_store(DEFAULT_COURSES)
        return DEFAULT_COURSES

    courses = raw.get("courses") if isinstance(raw, dict) else None
    if not isinstance(courses, list) or len(courses) < 2:
        write_academic_store(DEFAULT_COURSES)
        return DEFAULT_COURSES

    return [course for course in courses if isinstance(course, dict) and course.get("id")]


def get_course(course_id: str) -> dict[str, Any] | None:
    return next((course for course in load_courses() if course.get("id") == course_id), None)
