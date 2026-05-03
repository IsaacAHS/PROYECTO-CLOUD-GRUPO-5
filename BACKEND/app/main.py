from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.services.job_store import read_job, write_job
from app.services.script_payload import build_script_variables
from app.services.vm_placement import place_vms


app = FastAPI(title="NimbusCore API", version="0.1.0")

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


COURSES = [
    {"id": "TEL141", "nombre": "Ingenieria de Redes Cloud"},
    {"id": "TEL142", "nombre": "Comunicaciones Moviles"},
    {"id": "TEL143", "nombre": "Trabajo de Tesis 1"},
]

SLICES: dict[str, dict[str, Any]] = {
    "slice-demo-1": {
        "id": "slice-demo-1",
        "nombre": "Slice de Red A",
        "zona": "openstack-zone-1",
        "estado": "CREADO",
        "curso_id": None,
        "topologias": [{"type": "lineal", "count": 4}],
        "nodos": [],
        "enlaces": [],
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
}

DEPLOYMENTS: dict[str, dict[str, Any]] = {}


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
def list_courses() -> list[dict[str, str]]:
    return COURSES


@app.get("/slices")
def list_slices() -> list[dict[str, Any]]:
    return list(SLICES.values())


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
    app_log(
        f"slice creado id={slice_id} nombre={payload.nombre!r} "
        f"topologias={payload.topologias}"
    )
    return item


@app.get("/slices/{slice_id}")
def get_slice(slice_id: str) -> dict[str, Any]:
    item = SLICES.get(slice_id)
    if not item:
        raise HTTPException(status_code=404, detail="Slice no encontrado")
    return item


@app.put("/slices/{slice_id}")
def update_slice(slice_id: str, payload: SliceUpdate) -> dict[str, Any]:
    item = SLICES.get(slice_id)
    if not item:
        raise HTTPException(status_code=404, detail="Slice no encontrado")

    changes = payload.model_dump(exclude_unset=True)
    item.update(changes)
    item["updated_at"] = now_iso()
    return item


@app.delete("/slices/{slice_id}")
def delete_slice(slice_id: str) -> dict[str, str]:
    if slice_id not in SLICES:
        raise HTTPException(status_code=404, detail="Slice no encontrado")
    del SLICES[slice_id]
    return {"status": "deleted", "slice_id": slice_id}


@app.post("/slices/{slice_id}/deploy", status_code=202)
def deploy_slice(slice_id: str) -> dict[str, Any]:
    item = SLICES.get(slice_id)
    if not item:
        raise HTTPException(status_code=404, detail="Slice no encontrado")

    job_id = f"job-{uuid4().hex[:8]}"
    item["estado"] = "DESPLEGANDO"
    item["updated_at"] = now_iso()
    placements = place_vms(item)
    script_variables = build_script_variables(item, placements)

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
    write_job(deployment)
    app_log(
        f"deploy solicitado slice_id={slice_id} job_id={job_id} "
        f"job_dir=/jobs topologias={script_variables.get('topologies')}"
    )
    return deployment


@app.get("/deployments/{job_id}")
def get_deployment(job_id: str) -> dict[str, Any]:
    deployment = read_job(job_id) or DEPLOYMENTS.get(job_id)
    if not deployment:
        raise HTTPException(status_code=404, detail="Deployment no encontrado")

    slice_item = SLICES.get(deployment["slice_id"])
    if slice_item and deployment.get("status") == "SUCCESS":
        slice_item["estado"] = "ACTIVO"
        slice_item["updated_at"] = now_iso()

    return deployment


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
    return {"slice_id": slice_id, "action": action, "estado": item["estado"]}
