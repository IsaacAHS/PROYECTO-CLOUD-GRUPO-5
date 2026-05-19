import json
import math
import os
import re
import subprocess
import tempfile
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any
from urllib.parse import quote
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.services.academic_store import get_course, load_courses
from app.services.job_store import read_job, write_job
from app.services.image_catalog import list_images, upsert_image
from app.services.script_payload import build_script_variables
from app.services.slice_store import (
    load_deployments,
    load_slices,
    save_deployments,
    save_slices,
)
from app.services.slice_template_store import read_templates, write_templates
from app.services.vm_inventory_store import inventory_for_slice
from app.services.vm_placement import place_vms


app = FastAPI(title="NimbusCore API", version="0.1.0")
KEYPAIR_DIR = Path(os.getenv("NIMBUSCORE_KEYPAIR_DIR", "/keypairs"))
KEYPAIR_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
NOVNC_TOKEN_FILE = Path(os.getenv("NIMBUSCORE_NOVNC_TOKEN_FILE", "/novnc-tokens/tokens.cfg"))
NOVNC_PUBLIC_BASE = os.getenv("NIMBUSCORE_NOVNC_PUBLIC_BASE", "/novnc").rstrip("/")
IMAGE_UPLOAD_DIR = Path(os.getenv("NIMBUSCORE_IMAGE_UPLOAD_DIR", "/image-uploads"))
IMAGE_UPLOAD_SCRIPT = Path(
    os.getenv("NIMBUSCORE_IMAGE_UPLOAD_SCRIPT", "/image-runner/upload_image_to_drive.sh")
)
KEEP_IMAGE_UPLOADS = os.getenv("NIMBUSCORE_KEEP_IMAGE_UPLOADS", "false").lower() == "true"
IMAGE_UPLOAD_TIMEOUT = int(os.getenv("NIMBUSCORE_IMAGE_UPLOAD_TIMEOUT", "3600"))
IMAGE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
ALLOWED_IMAGE_SUFFIXES = {".img", ".qcow2", ".iso"}
ACTIVE_JOB_STATUSES = {"QUEUED", "RUNNING"}
DEPLOY_BLOCKING_SLICE_STATES = {"DESPLEGANDO", "ACTIVO", "DESTRUYENDO", "REINICIANDO"}
DESTROY_BLOCKING_SLICE_STATES = {"DESPLEGANDO", "DESTRUYENDO", "REINICIANDO", "DESTRUIDO"}
DEPLOYMENT_LOCK = Lock()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def app_log(message: str) -> None:
    print(f"[{now_iso()}] {message}", flush=True)


def ensure_keypair_dir() -> None:
    KEYPAIR_DIR.mkdir(parents=True, exist_ok=True)


def validate_keypair_name(name: str) -> str:
    clean_name = name.strip()
    if not KEYPAIR_NAME_RE.match(clean_name):
        raise HTTPException(
            status_code=400,
            detail="Nombre invalido. Usa letras, numeros, punto, guion o guion bajo.",
        )
    return clean_name


def sanitize_uploaded_image_name(filename: str) -> str:
    clean_name = Path(filename or "").name.strip()
    clean_name = re.sub(r"[^A-Za-z0-9._-]+", "-", clean_name).strip("-._")
    if not clean_name:
        raise HTTPException(status_code=400, detail="Nombre de archivo invalido.")
    suffix = Path(clean_name).suffix.lower()
    if suffix not in ALLOWED_IMAGE_SUFFIXES:
        allowed = ", ".join(sorted(ALLOWED_IMAGE_SUFFIXES))
        raise HTTPException(status_code=400, detail=f"Extension no soportada. Usa: {allowed}")
    return clean_name


def image_id_from_filename(filename: str) -> str:
    base_name = Path(filename).stem.lower()
    image_id = re.sub(r"[^a-z0-9._-]+", "-", base_name).strip("-._")
    image_id = image_id[:64].strip("-._") or f"image-{uuid4().hex[:8]}"
    if not IMAGE_ID_RE.match(image_id):
        image_id = f"image-{uuid4().hex[:8]}"
    return image_id


async def write_upload_to_disk(upload: UploadFile, target: Path) -> int:
    total = 0
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("wb") as output:
        while True:
            chunk = await upload.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            output.write(chunk)
    return total


def run_image_upload_script(source_file: Path, dest_name: str) -> dict[str, Any]:
    if not IMAGE_UPLOAD_SCRIPT.exists():
        raise HTTPException(
            status_code=500,
            detail=f"Script de subida no encontrado: {IMAGE_UPLOAD_SCRIPT}",
        )

    result = subprocess.run(
        ["sh", str(IMAGE_UPLOAD_SCRIPT), str(source_file), dest_name],
        check=False,
        capture_output=True,
        text=True,
        timeout=IMAGE_UPLOAD_TIMEOUT,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "Error ejecutando rclone.").strip()
        raise HTTPException(status_code=500, detail=detail)

    output = result.stdout.strip().splitlines()
    if not output:
        raise HTTPException(status_code=500, detail="El script de rclone no devolvio metadata.")
    try:
        return json.loads(output[-1])
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"No se pudo parsear la respuesta de rclone: {output[-1]}",
        ) from exc


def keypair_item(public_key_path: Path) -> dict[str, str]:
    public_key = public_key_path.read_text(encoding="utf-8").strip()
    return {
        "name": public_key_path.stem,
        "public_key": public_key,
        "path": str(public_key_path),
    }


class LoginRequest(BaseModel):
    username: str
    password: str


class SliceCreate(BaseModel):
    nombre: str = Field(min_length=1)
    zona: str = "openstack-zone-1"
    topologias: list[dict[str, Any]] = Field(default_factory=list)
    nodos: list[dict[str, Any]] = Field(default_factory=list)
    enlaces: list[dict[str, Any]] = Field(default_factory=list)
    curso_id: str | None = None


class SliceUpdate(BaseModel):
    nombre: str | None = None
    zona: str | None = None
    topologias: list[dict[str, Any]] | None = None
    nodos: list[dict[str, Any]] | None = None
    enlaces: list[dict[str, Any]] | None = None
    curso_id: str | None = None


class KeypairCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)


class ImageCreate(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    label: str | None = None
    url: str = Field(min_length=1)
    download_method: str = "auto"
    cloud_init: bool | None = None
    cloud_init_mode: str | None = None
    min_disk_gb: int | None = None
    active: bool = True


class AssignTemplateRequest(BaseModel):
    course_id: str = Field(min_length=1)


SLICES: dict[str, dict[str, Any]] = load_slices()
DEPLOYMENTS: dict[str, dict[str, Any]] = load_deployments()
SLICE_TEMPLATES: dict[str, dict[str, Any]] = read_templates()


def course_has_assigned_slices(course_id: str) -> bool:
    return any(
        str(slice_item.get("curso_id") or "") == str(course_id)
        for slice_item in SLICES.values()
    )


def find_active_deployment_for_slice(slice_id: str) -> dict[str, Any] | None:
    changed = False
    active_deployment = None

    for job_id, deployment in list(DEPLOYMENTS.items()):
        current = deployment
        job_deployment = read_job(job_id)
        if job_deployment:
            current = job_deployment
            if DEPLOYMENTS.get(job_id) != job_deployment:
                DEPLOYMENTS[job_id] = job_deployment
                changed = True

        if str(current.get("slice_id")) != str(slice_id):
            continue

        status = str(current.get("status") or "").upper()
        if status in ACTIVE_JOB_STATUSES:
            active_deployment = current
            break

    if changed:
        save_deployments(DEPLOYMENTS)

    return active_deployment


def refresh_deployment_from_job(job_id: str, deployment: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    job_deployment = read_job(job_id)
    if job_deployment and DEPLOYMENTS.get(job_id) != job_deployment:
        DEPLOYMENTS[job_id] = job_deployment
        return job_deployment, True
    return job_deployment or deployment, False


def deployment_created_at(deployment: dict[str, Any]) -> str:
    return str(deployment.get("created_at") or deployment.get("updated_at") or "")


def latest_deployment_for_slice(slice_id: str) -> tuple[dict[str, Any] | None, bool]:
    changed = False
    matches: list[dict[str, Any]] = []

    for job_id, deployment in list(DEPLOYMENTS.items()):
        current, current_changed = refresh_deployment_from_job(job_id, deployment)
        changed = changed or current_changed
        if str(current.get("slice_id")) == str(slice_id):
            matches.append(current)

    matches.sort(key=deployment_created_at, reverse=True)
    return (matches[0] if matches else None), changed


def state_from_deployment(deployment: dict[str, Any]) -> str | None:
    status = str(deployment.get("status") or "").upper()
    action = deployment.get("script_runner", {}).get("action")

    if status in ACTIVE_JOB_STATUSES:
        return "DESTRUYENDO" if action == "destroy_topology" else "DESPLEGANDO"
    if status == "SUCCESS":
        return "DESTRUIDO" if action == "destroy_topology" else "ACTIVO"
    if status == "FAILED":
        return "ERROR"
    return None


def state_from_inventory(inventory: dict[str, Any] | None) -> str | None:
    if not inventory:
        return None

    status = str(inventory.get("status") or "").upper()
    if status == "SUCCESS":
        return "ACTIVO"
    if status == "DESTROYED":
        return "DESTRUIDO"
    if status == "DESTROYING":
        return "DESTRUYENDO"
    if status == "FAILED":
        return "ERROR"
    if status == "RUNNING":
        return "DESPLEGANDO"
    return None


def reconcile_slice_runtime_state(item: dict[str, Any]) -> bool:
    slice_id = str(item.get("id") or "")
    if not slice_id:
        return False

    changed = False
    deployment, deployments_changed = latest_deployment_for_slice(slice_id)
    changed = changed or deployments_changed

    desired_state = state_from_deployment(deployment) if deployment else None
    inventory = get_runtime_inventory(slice_id) or item.get("vm_inventory")
    if inventory:
        item["vm_inventory"] = inventory
    if desired_state is None:
        desired_state = state_from_inventory(inventory)

    if desired_state and str(item.get("estado") or "").upper() != desired_state:
        item["estado"] = desired_state
        item["updated_at"] = now_iso()
        changed = True

    return changed


def ensure_slice_can_start_deploy(slice_id: str, item: dict[str, Any]) -> None:
    active_deployment = find_active_deployment_for_slice(slice_id)
    if active_deployment:
        raise HTTPException(
            status_code=409,
            detail=(
                "El slice ya tiene un job activo "
                f"({active_deployment.get('status')}: {active_deployment.get('id')})."
            ),
        )

    state = str(item.get("estado") or "").upper()
    if state in DEPLOY_BLOCKING_SLICE_STATES:
        raise HTTPException(
            status_code=409,
            detail=f"El slice esta en estado {state}; no se puede desplegar nuevamente.",
        )


def ensure_slice_can_start_destroy(slice_id: str, item: dict[str, Any]) -> None:
    active_deployment = find_active_deployment_for_slice(slice_id)
    if active_deployment:
        raise HTTPException(
            status_code=409,
            detail=(
                "El slice ya tiene un job activo "
                f"({active_deployment.get('status')}: {active_deployment.get('id')})."
            ),
        )

    state = str(item.get("estado") or "").upper()
    if state in DESTROY_BLOCKING_SLICE_STATES:
        raise HTTPException(
            status_code=409,
            detail=f"El slice esta en estado {state}; no se puede apagar nuevamente.",
        )


def empty_inventory(slice_id: str, status: str = "PENDING") -> dict[str, Any]:
    return {
        "slice_id": slice_id,
        "status": status,
        "vms": [],
        "networks": [],
        "topologies": [],
    }


def find_inventory_vm(slice_id: str, vm_name: str) -> dict[str, Any] | None:
    inventory = get_runtime_inventory(slice_id)
    if not inventory:
        item = SLICES.get(slice_id)
        inventory = item.get("vm_inventory") if item else None
    if not inventory:
        return None

    for vm in inventory.get("vms", []):
        if str(vm.get("name")) == vm_name or str(vm.get("id")) == vm_name:
            return vm
    return None


def write_novnc_token(token: str, target_host: str, target_port: int) -> None:
    NOVNC_TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    existing_lines: list[str] = []
    if NOVNC_TOKEN_FILE.exists():
        existing_lines = NOVNC_TOKEN_FILE.read_text(encoding="utf-8").splitlines()

    prefix = f"{token}:"
    lines = [line for line in existing_lines if not line.startswith(prefix)]
    lines.append(f"{token}: {target_host}:{target_port}")
    NOVNC_TOKEN_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def get_runtime_inventory(slice_id: str) -> dict[str, Any] | None:
    return inventory_for_slice(slice_id)


def enrich_slice(item: dict[str, Any]) -> dict[str, Any]:
    reconcile_slice_runtime_state(item)
    enriched = dict(item)
    inventory = get_runtime_inventory(str(item["id"])) or item.get("vm_inventory")
    if inventory:
        enriched["vm_inventory"] = inventory
        enriched["vms"] = inventory.get("vms", [])
        enriched["runtime_status"] = inventory.get("status")
    return enriched


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/auth/login")
def login(payload: LoginRequest) -> dict[str, Any]:
    if not payload.username or not payload.password:
        raise HTTPException(status_code=400, detail="Credenciales incompletas")

    return {
        "access_token": "demo-token-nimbuscore",
        "token_type": "bearer",
        "user": {
            "id": "user-demo",
            "name": "Administrador Demo",
            "role": "admin",
        },
    }


@app.get("/cursos")
def list_courses() -> list[dict[str, Any]]:
    return load_courses()


@app.get("/cursos/{course_id}")
def get_course_detail(course_id: str) -> dict[str, Any]:
    course = get_course(course_id)
    if not course:
        raise HTTPException(status_code=404, detail="Curso no encontrado")
    return course


@app.get("/cursos/{course_id}/alumnos")
def list_course_students(course_id: str) -> list[dict[str, Any]]:
    course = get_course(course_id)
    if not course:
        raise HTTPException(status_code=404, detail="Curso no encontrado")
    return course.get("alumnos", [])


@app.get("/keypairs")
def list_keypairs() -> list[dict[str, str]]:
    ensure_keypair_dir()
    keypairs = []
    for public_key_path in sorted(KEYPAIR_DIR.glob("*.pub")):
        if public_key_path.is_file():
            keypairs.append(keypair_item(public_key_path))
    return keypairs


@app.post("/keypairs", status_code=201)
def create_keypair(payload: KeypairCreate) -> dict[str, str]:
    ensure_keypair_dir()
    name = validate_keypair_name(payload.name)
    public_key_path = KEYPAIR_DIR / f"{name}.pub"

    if public_key_path.exists():
        raise HTTPException(status_code=409, detail="El par de llaves ya existe")

    with tempfile.TemporaryDirectory() as tmp_dir:
        private_key_path = Path(tmp_dir) / name
        try:
            subprocess.run(
                [
                    "ssh-keygen",
                    "-t",
                    "ecdsa",
                    "-b",
                    "256",
                    "-f",
                    str(private_key_path),
                    "-N",
                    "",
                    "-C",
                    name,
                ],
                check=True,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError as exc:
            raise HTTPException(
                status_code=500,
                detail="ssh-keygen no esta instalado en el backend",
            ) from exc
        except subprocess.CalledProcessError as exc:
            raise HTTPException(status_code=500, detail=exc.stderr.strip()) from exc

        generated_public_key_path = Path(f"{private_key_path}.pub")
        public_key = generated_public_key_path.read_text(encoding="utf-8")
        private_key = private_key_path.read_text(encoding="utf-8")

    public_key_path.write_text(public_key, encoding="utf-8")
    public_key_path.chmod(0o644)
    app_log(f"keypair creado name={name} public_key={public_key_path}")

    return {
        "name": name,
        "public_key": public_key.strip(),
        "private_key": private_key,
    }


@app.get("/images")
def get_images() -> list[dict[str, Any]]:
    return list_images()


@app.post("/images", status_code=201)
def create_image(payload: ImageCreate) -> dict[str, Any]:
    try:
        image = upsert_image(payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    app_log(f"imagen registrada id={image['id']} method={image['download_method']}")
    return image


@app.post("/images/upload", status_code=201)
async def upload_image_to_drive(
    file: UploadFile = File(...),
    image_id: str | None = Form(None),
    label: str | None = Form(None),
    active: bool = Form(True),
) -> dict[str, Any]:
    original_name = sanitize_uploaded_image_name(file.filename or "")
    clean_id = (image_id or image_id_from_filename(original_name)).strip()
    if not IMAGE_ID_RE.match(clean_id):
        raise HTTPException(
            status_code=400,
            detail="ID de imagen invalido. Usa letras, numeros, punto, guion o guion bajo.",
        )
    if any(image["id"] == clean_id for image in list_images(include_inactive=True)):
        await file.close()
        raise HTTPException(
            status_code=409,
            detail=(
                f"Ya existe una imagen registrada como {clean_id}. "
                "Renombra el archivo antes de subirlo."
            ),
        )

    suffix = Path(original_name).suffix.lower()
    drive_name = f"{clean_id}-{uuid4().hex[:8]}{suffix}"
    temp_name = f"{uuid4().hex}-{original_name}"
    temp_path = IMAGE_UPLOAD_DIR / temp_name

    try:
        size_bytes = await write_upload_to_disk(file, temp_path)
        if size_bytes <= 0:
            raise HTTPException(status_code=400, detail="La imagen subida esta vacia.")

        upload_result = run_image_upload_script(temp_path, drive_name)
        try:
            image = upsert_image(
                {
                    "id": clean_id,
                    "name": upload_result.get("name") or drive_name,
                    "label": label or original_name,
                    "url": upload_result["download_url"],
                    "download_method": upload_result.get(
                        "download_method", "wget-no-check-certificate"
                    ),
                    "cloud_init": True,
                    "cloud_init_mode": "full",
                    "min_disk_gb": max(1, math.ceil(size_bytes / (1024**3))),
                    "active": active,
                    "source": "google-drive-rclone",
                    "drive_file_id": upload_result.get("file_id"),
                    "drive_public_link": upload_result.get("public_link"),
                    "drive_target": upload_result.get("target"),
                    "drive_remote": upload_result.get("remote"),
                    "drive_folder": upload_result.get("folder"),
                    "size_bytes": upload_result.get("size_bytes") or size_bytes,
                    "uploaded_at": now_iso(),
                }
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        await file.close()
        if not KEEP_IMAGE_UPLOADS:
            temp_path.unlink(missing_ok=True)

    app_log(
        f"imagen subida id={image['id']} file={image['name']} "
        f"drive_file_id={image.get('drive_file_id')}"
    )
    return image


@app.get("/slice-templates")
def list_slice_templates() -> list[dict[str, Any]]:
    return list(SLICE_TEMPLATES.values())


@app.post("/slice-templates", status_code=201)
def create_slice_template(payload: SliceCreate) -> dict[str, Any]:
    template_id = f"tpl-{uuid4().hex[:8]}"
    item = {
        "id": template_id,
        "nombre": payload.nombre,
        "zona": payload.zona,
        "estado": "PLANTILLA",
        "curso_id": payload.curso_id,
        "topologias": payload.topologias,
        "nodos": payload.nodos,
        "enlaces": payload.enlaces,
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    SLICE_TEMPLATES[template_id] = item
    write_templates(SLICE_TEMPLATES)
    app_log(f"template creado id={template_id} nombre={payload.nombre!r}")
    return item


@app.get("/slice-templates/{template_id}")
def get_slice_template(template_id: str) -> dict[str, Any]:
    item = SLICE_TEMPLATES.get(template_id)
    if not item:
        raise HTTPException(status_code=404, detail="Plantilla no encontrada")
    return item


@app.put("/slice-templates/{template_id}")
def update_slice_template(template_id: str, payload: SliceUpdate) -> dict[str, Any]:
    item = SLICE_TEMPLATES.get(template_id)
    if not item:
        raise HTTPException(status_code=404, detail="Plantilla no encontrada")

    item.update(payload.model_dump(exclude_unset=True))
    item["estado"] = "PLANTILLA"
    item["updated_at"] = now_iso()
    write_templates(SLICE_TEMPLATES)
    return item


@app.delete("/slice-templates/{template_id}")
def delete_slice_template(template_id: str) -> dict[str, str]:
    if template_id not in SLICE_TEMPLATES:
        raise HTTPException(status_code=404, detail="Plantilla no encontrada")
    del SLICE_TEMPLATES[template_id]
    write_templates(SLICE_TEMPLATES)
    return {"status": "deleted", "template_id": template_id}


@app.post("/slice-templates/{template_id}/assign-to-course", status_code=201)
def assign_template_to_course(
    template_id: str, payload: AssignTemplateRequest
) -> dict[str, Any]:
    template = SLICE_TEMPLATES.get(template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Plantilla no encontrada")

    course = get_course(payload.course_id)
    if not course:
        raise HTTPException(status_code=404, detail="Curso no encontrado")

    if course_has_assigned_slices(course["id"]):
        raise HTTPException(
            status_code=409,
            detail="Este curso ya tiene un set de slices asignado.",
        )

    created = []
    for student in course.get("alumnos", []):
        slice_id = f"slice-{uuid4().hex[:8]}"
        item = {
            "id": slice_id,
            "template_id": template_id,
            "nombre": f"{template['nombre']} - {student['nombre']}",
            "zona": template.get("zona") or "openstack-zone-1",
            "estado": "ASIGNADO",
            "curso_id": course["id"],
            "curso_nombre": course["nombre"],
            "alumno_id": student["id"],
            "alumno_nombre": student["nombre"],
            "topologias": deepcopy(template.get("topologias") or []),
            "nodos": deepcopy(template.get("nodos") or []),
            "enlaces": deepcopy(template.get("enlaces") or []),
            "created_at": now_iso(),
            "updated_at": now_iso(),
        }
        SLICES[slice_id] = item
        created.append(item)

    template.setdefault("assignments", []).append(
        {
            "course_id": course["id"],
            "course_name": course["nombre"],
            "slice_ids": [item["id"] for item in created],
            "created_at": now_iso(),
        }
    )
    template["updated_at"] = now_iso()
    save_slices(SLICES)
    write_templates(SLICE_TEMPLATES)
    app_log(
        f"template asignado template_id={template_id} curso={course['id']} "
        f"slices={len(created)}"
    )
    return {"template": template, "course": course, "slices": created}


@app.get("/slices")
def list_slices() -> list[dict[str, Any]]:
    enriched = [enrich_slice(item) for item in SLICES.values()]
    save_slices(SLICES)
    save_deployments(DEPLOYMENTS)
    return enriched


@app.post("/slices", status_code=201)
def create_slice(payload: SliceCreate) -> dict[str, Any]:
    slice_id = f"slice-{uuid4().hex[:8]}"
    item = {
        "id": slice_id,
        "nombre": payload.nombre,
        "zona": payload.zona,
        "estado": "CREADO",
        "curso_id": payload.curso_id,
        "topologias": payload.topologias,
        "nodos": payload.nodos,
        "enlaces": payload.enlaces,
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    SLICES[slice_id] = item
    save_slices(SLICES)
    app_log(
        f"slice creado id={slice_id} nombre={payload.nombre!r} "
        f"topologias={payload.topologias}"
    )
    return enrich_slice(item)


@app.get("/slices/{slice_id}")
def get_slice(slice_id: str) -> dict[str, Any]:
    item = SLICES.get(slice_id)
    if not item:
        raise HTTPException(status_code=404, detail="Slice no encontrado")
    enriched = enrich_slice(item)
    save_slices(SLICES)
    save_deployments(DEPLOYMENTS)
    return enriched


@app.put("/slices/{slice_id}")
def update_slice(slice_id: str, payload: SliceUpdate) -> dict[str, Any]:
    item = SLICES.get(slice_id)
    if not item:
        raise HTTPException(status_code=404, detail="Slice no encontrado")

    changes = payload.model_dump(exclude_unset=True)
    item.update(changes)
    item["updated_at"] = now_iso()
    save_slices(SLICES)
    return enrich_slice(item)


@app.delete("/slices/{slice_id}")
def delete_slice(slice_id: str) -> dict[str, str]:
    if slice_id not in SLICES:
        raise HTTPException(status_code=404, detail="Slice no encontrado")
    del SLICES[slice_id]
    save_slices(SLICES)
    return {"status": "deleted", "slice_id": slice_id}


@app.post("/slices/{slice_id}/deploy", status_code=202)
def deploy_slice(slice_id: str) -> dict[str, Any]:
    item = SLICES.get(slice_id)
    if not item:
        raise HTTPException(status_code=404, detail="Slice no encontrado")

    with DEPLOYMENT_LOCK:
        if reconcile_slice_runtime_state(item):
            save_slices(SLICES)
            save_deployments(DEPLOYMENTS)
        ensure_slice_can_start_deploy(slice_id, item)

        job_id = f"job-{uuid4().hex[:8]}"
        item["estado"] = "DESPLEGANDO"
        item["vm_inventory"] = empty_inventory(slice_id, "QUEUED")
        item["updated_at"] = now_iso()
        save_slices(SLICES)
        placements = place_vms(item)
        try:
            script_variables = build_script_variables(item, placements)
        except ValueError as exc:
            item["estado"] = "ERROR"
            item["vm_inventory"] = empty_inventory(slice_id, "FAILED")
            item["vm_inventory"]["message"] = str(exc)
            item["updated_at"] = now_iso()
            save_slices(SLICES)
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        deployment = {
            "id": job_id,
            "slice_id": slice_id,
            "status": "QUEUED",
            "message": "Job creado. El worker ejecutara Script Runner segun la configuracion actual.",
            "placements": placements,
            "script_runner": {
                "action": "create_topology",
                "variables": script_variables,
            },
            "created_at": now_iso(),
            "updated_at": now_iso(),
        }
        DEPLOYMENTS[job_id] = deployment
        save_deployments(DEPLOYMENTS)
        write_job(deployment)

    app_log(
        f"deploy solicitado slice_id={slice_id} job_id={job_id} "
        f"job_dir=/jobs topologias={script_variables.get('topologies')}"
    )
    return deployment


@app.post("/slices/{slice_id}/destroy", status_code=202)
def destroy_slice(slice_id: str) -> dict[str, Any]:
    item = SLICES.get(slice_id)
    if not item:
        raise HTTPException(status_code=404, detail="Slice no encontrado")

    with DEPLOYMENT_LOCK:
        if reconcile_slice_runtime_state(item):
            save_slices(SLICES)
            save_deployments(DEPLOYMENTS)
        ensure_slice_can_start_destroy(slice_id, item)

        inventory = get_runtime_inventory(slice_id) or item.get("vm_inventory")
        if not inventory or (not inventory.get("vms") and not inventory.get("networks")):
            raise HTTPException(
                status_code=409,
                detail="El slice no tiene inventario de VMs o redes para destruir",
            )

        job_id = f"job-{uuid4().hex[:8]}"
        item["estado"] = "DESTRUYENDO"
        item["updated_at"] = now_iso()
        save_slices(SLICES)

        deployment = {
            "id": job_id,
            "slice_id": slice_id,
            "status": "QUEUED",
            "message": "Job creado. El worker destruira las VMs registradas en el inventario.",
            "script_runner": {
                "action": "destroy_topology",
                "variables": {
                    "inventory": deepcopy(inventory),
                },
                "vm_inventory": deepcopy(inventory),
            },
            "created_at": now_iso(),
            "updated_at": now_iso(),
        }
        DEPLOYMENTS[job_id] = deployment
        save_deployments(DEPLOYMENTS)
        write_job(deployment)

    app_log(f"destroy solicitado slice_id={slice_id} job_id={job_id}")
    return deployment


@app.get("/deployments/{job_id}")
def get_deployment(job_id: str) -> dict[str, Any]:
    job_deployment = read_job(job_id)
    deployment = job_deployment or DEPLOYMENTS.get(job_id)
    if not deployment:
        raise HTTPException(status_code=404, detail="Deployment no encontrado")

    if job_deployment:
        DEPLOYMENTS[job_id] = job_deployment
        save_deployments(DEPLOYMENTS)

    slice_item = SLICES.get(deployment["slice_id"])
    inventory = get_runtime_inventory(deployment["slice_id"])
    if not inventory:
        inventory = deployment.get("script_runner", {}).get("vm_inventory")
    if inventory:
        deployment["vm_inventory"] = inventory
        if slice_item:
            slice_item["vm_inventory"] = inventory

    action = deployment.get("script_runner", {}).get("action")
    if slice_item and deployment.get("status") == "SUCCESS":
        slice_item["estado"] = "DESTRUIDO" if action == "destroy_topology" else "ACTIVO"
        slice_item["updated_at"] = now_iso()
        save_slices(SLICES)
    elif slice_item and deployment.get("status") == "FAILED":
        slice_item["estado"] = "ERROR"
        slice_item["updated_at"] = now_iso()
        save_slices(SLICES)
    elif slice_item and inventory:
        slice_item["updated_at"] = now_iso()
        save_slices(SLICES)

    return deployment


@app.get("/slices/{slice_id}/inventory")
def get_slice_inventory(slice_id: str) -> dict[str, Any]:
    item = SLICES.get(slice_id)
    if not item:
        raise HTTPException(status_code=404, detail="Slice no encontrado")
    return get_runtime_inventory(slice_id) or item.get("vm_inventory") or empty_inventory(slice_id)


@app.post("/slices/{slice_id}/vms/{vm_name}/console", status_code=201)
def create_vm_console(slice_id: str, vm_name: str) -> dict[str, Any]:
    if slice_id not in SLICES:
        raise HTTPException(status_code=404, detail="Slice no encontrado")

    vm = find_inventory_vm(slice_id, vm_name)
    if not vm:
        raise HTTPException(status_code=404, detail="VM no encontrada en inventario")

    target_host = str(vm.get("worker_ip") or "").strip()
    try:
        target_port = int(vm.get("vnc_port") or 0)
    except (TypeError, ValueError):
        target_port = 0

    if not target_host or target_port <= 0:
        raise HTTPException(status_code=409, detail="La VM no tiene target VNC valido")

    token = f"console-{uuid4().hex[:16]}"
    write_novnc_token(token, target_host, target_port)
    websocket_path = f"novnc/websockify?token={quote(token)}"
    console_url = (
        f"{NOVNC_PUBLIC_BASE}/vnc.html"
        f"?autoconnect=1&resize=remote&reconnect=1"
        f"&path={quote(websocket_path, safe='')}"
    )
    app_log(
        f"console creada slice_id={slice_id} vm={vm_name} "
        f"target={target_host}:{target_port} token={token}"
    )
    return {
        "slice_id": slice_id,
        "vm_name": vm.get("name") or vm_name,
        "target": f"{target_host}:{target_port}",
        "token": token,
        "url": console_url,
    }


@app.post("/slices/{slice_id}/{action}")
def slice_action(slice_id: str, action: str) -> dict[str, str]:
    item = SLICES.get(slice_id)
    if not item:
        raise HTTPException(status_code=404, detail="Slice no encontrado")

    states = {
        "start": "ACTIVO",
        "stop": "DETENIDO",
        "restart": "REINICIANDO",
        "destroy": "DESTRUIDO",
    }
    if action not in states:
        raise HTTPException(status_code=400, detail="Accion no soportada")

    item["estado"] = states[action]
    item["updated_at"] = now_iso()
    save_slices(SLICES)
    return {"slice_id": slice_id, "action": action, "estado": item["estado"]}
