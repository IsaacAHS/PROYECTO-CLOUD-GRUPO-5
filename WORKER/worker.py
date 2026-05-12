import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from script_runner import format_command, run_script_command
from topology_mapper import destroy_commands_for_job, inventory_for_job, script_commands_for_job


JOB_DIR = Path(os.getenv("NIMBUSCORE_JOB_DIR", "/jobs"))
RUN_DIR = Path(os.getenv("NIMBUSCORE_RUN_DIR", "/script-runs"))
POLL_SECONDS = int(os.getenv("NIMBUSCORE_WORKER_POLL_SECONDS", "3"))
DEFAULT_VNC_BASE = int(os.getenv("NIMBUSCORE_VNC_BASE", "5901"))
VNC_BLOCK_SIZE = int(os.getenv("NIMBUSCORE_VNC_BLOCK_SIZE", "100"))
DEFAULT_VLAN_BASE = int(os.getenv("NIMBUSCORE_VLAN_BASE", "100"))
VLAN_BLOCK_SIZE = int(os.getenv("NIMBUSCORE_VLAN_BLOCK_SIZE", "100"))
DEFAULT_CIDR_BASE = int(os.getenv("NIMBUSCORE_CIDR_BASE", "10"))
CIDR_BLOCK_SIZE = int(os.getenv("NIMBUSCORE_CIDR_BLOCK_SIZE", "20"))
VNC_ALLOCATIONS_PATH = RUN_DIR / "vnc_allocations.json"
NETWORK_ALLOCATIONS_PATH = RUN_DIR / "network_allocations.json"
VM_INVENTORY_PATH = Path(os.getenv("NIMBUSCORE_VM_INVENTORY_PATH", str(RUN_DIR / "vm_inventory.json")))
MAX_VLAN_ID = 4094
MAX_CIDR_THIRD_OCTET = 254


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def log(message: str) -> None:
    print(f"[{now_iso()}] {message}", flush=True)


def read_json(path: Path, default: dict) -> dict:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def write_vm_inventory(job: dict, inventory: dict, status: str, message: str = "") -> None:
    inventory["status"] = status
    inventory["message"] = message
    inventory["updated_at"] = now_iso()
    inventory["job_id"] = job["id"]
    inventory["slice_id"] = job.get("slice_id")

    VM_INVENTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    state = read_json(VM_INVENTORY_PATH, {"updated_at": now_iso(), "slices": {}})
    slices = state.setdefault("slices", {})
    key = job.get("slice_id") or job["id"]
    slices[key] = dict(inventory)
    state["updated_at"] = now_iso()
    write_json(VM_INVENTORY_PATH, state)


def mark_topology_vms(inventory: dict, topology_index: int, status: str) -> None:
    for vm in inventory.get("vms", []):
        if int(vm.get("topology_index") or -1) == topology_index:
            vm["status"] = status
            vm["updated_at"] = now_iso()


def mark_pending_topology_vms_failed(inventory: dict, failed_topology_index: int) -> None:
    for vm in inventory.get("vms", []):
        topology_index = int(vm.get("topology_index") or -1)
        if topology_index == failed_topology_index or vm.get("status") == "PLANNED":
            vm["status"] = "FAILED"
            vm["updated_at"] = now_iso()


def mark_all_vms(inventory: dict, status: str) -> None:
    for vm in inventory.get("vms", []):
        vm["status"] = status
        vm["updated_at"] = now_iso()


def mark_all_networks(inventory: dict, status: str) -> None:
    for network in inventory.get("networks", []):
        network["status"] = status
        network["updated_at"] = now_iso()


def estimate_vm_count(job: dict) -> int:
    variables = job.get("script_runner", {}).get("variables", {})
    topologies = variables.get("topologies") or []
    total = 0
    for topology in topologies:
        try:
            total += int(topology.get("count") or 0)
        except (TypeError, ValueError):
            continue
    return max(total, 1)


def estimate_network_count(job: dict) -> int:
    variables = job.get("script_runner", {}).get("variables", {})
    topologies = variables.get("topologies") or []
    total = 0

    for topology in topologies:
        try:
            node_count = int(topology.get("count") or 0)
        except (TypeError, ValueError):
            continue

        if node_count <= 0:
            continue

        topology_type = topology.get("type")
        if topology_type == "lineal":
            total += max(node_count - 1, 1)
        elif topology_type == "anillo":
            total += node_count
        else:
            total += max(node_count, 1)

    return max(total, 1)


def allocate_vnc_base(job: dict) -> int:
    script_data = job.setdefault("script_runner", {})
    variables = script_data.setdefault("variables", {})
    if variables.get("vnc_base"):
        return int(variables["vnc_base"])

    RUN_DIR.mkdir(parents=True, exist_ok=True)
    state = read_json(
        VNC_ALLOCATIONS_PATH,
        {"next_base": DEFAULT_VNC_BASE, "allocations": {}},
    )
    allocations = state.setdefault("allocations", {})
    allocation_key = job.get("slice_id") or job["id"]

    if allocation_key in allocations:
        allocation = allocations[allocation_key]
        base = int(allocation["base"])
    else:
        base = int(state.get("next_base") or DEFAULT_VNC_BASE)
        block_size = max(VNC_BLOCK_SIZE, estimate_vm_count(job) + 5)
        allocation = {
            "base": base,
            "block_size": block_size,
            "job_id": job["id"],
            "slice_id": job.get("slice_id"),
            "created_at": now_iso(),
        }
        allocations[allocation_key] = allocation
        state["next_base"] = base + block_size
        write_json(VNC_ALLOCATIONS_PATH, state)

    variables["vnc_base"] = base
    variables["vnc_block_size"] = int(allocation.get("block_size") or VNC_BLOCK_SIZE)
    log(
        f"job={job['id']} vnc_base={variables['vnc_base']} "
        f"vnc_block_size={variables['vnc_block_size']}"
    )
    return base


def allocate_network_bases(job: dict) -> tuple[int, int]:
    script_data = job.setdefault("script_runner", {})
    variables = script_data.setdefault("variables", {})
    if variables.get("vlan_base") and variables.get("cidr_base"):
        return int(variables["vlan_base"]), int(variables["cidr_base"])

    RUN_DIR.mkdir(parents=True, exist_ok=True)
    required_networks = estimate_network_count(job)
    state = read_json(
        NETWORK_ALLOCATIONS_PATH,
        {
            "next_vlan_base": DEFAULT_VLAN_BASE,
            "next_cidr_base": DEFAULT_CIDR_BASE,
            "allocations": {},
        },
    )
    allocations = state.setdefault("allocations", {})
    allocation_key = job.get("slice_id") or job["id"]

    if allocation_key in allocations:
        allocation = allocations[allocation_key]
        vlan_base = int(allocation["vlan_base"])
        cidr_base = int(allocation["cidr_base"])
    else:
        vlan_base = int(state.get("next_vlan_base") or DEFAULT_VLAN_BASE)
        cidr_base = int(state.get("next_cidr_base") or DEFAULT_CIDR_BASE)
        vlan_block_size = max(VLAN_BLOCK_SIZE, required_networks + 5)
        cidr_block_size = max(CIDR_BLOCK_SIZE, required_networks + 2)

        if vlan_base + vlan_block_size - 1 > MAX_VLAN_ID:
            raise ValueError(
                "No hay VLAN_BASE disponible para reservar otro bloque "
                f"desde VLAN {vlan_base}."
            )

        if cidr_base + cidr_block_size - 1 > MAX_CIDR_THIRD_OCTET:
            raise ValueError(
                "No hay CIDR_BASE disponible para reservar otro bloque "
                f"desde 192.168.{cidr_base}.0/24."
            )

        allocation = {
            "vlan_base": vlan_base,
            "vlan_block_size": vlan_block_size,
            "cidr_base": cidr_base,
            "cidr_block_size": cidr_block_size,
            "required_networks": required_networks,
            "job_id": job["id"],
            "slice_id": job.get("slice_id"),
            "created_at": now_iso(),
        }
        allocations[allocation_key] = allocation
        state["next_vlan_base"] = vlan_base + vlan_block_size
        state["next_cidr_base"] = cidr_base + cidr_block_size
        write_json(NETWORK_ALLOCATIONS_PATH, state)

    variables["vlan_base"] = vlan_base
    variables["vlan_block_size"] = int(allocation.get("vlan_block_size") or VLAN_BLOCK_SIZE)
    variables["cidr_base"] = cidr_base
    variables["cidr_block_size"] = int(allocation.get("cidr_block_size") or CIDR_BLOCK_SIZE)
    log(
        f"job={job['id']} vlan_base={variables['vlan_base']} "
        f"vlan_block_size={variables['vlan_block_size']} "
        f"cidr_base={variables['cidr_base']} "
        f"cidr_block_size={variables['cidr_block_size']}"
    )
    return vlan_base, cidr_base


def process_script_job(path: Path, job: dict) -> None:
    job_run_dir = RUN_DIR / job["id"]
    job_run_dir.mkdir(parents=True, exist_ok=True)

    allocate_vnc_base(job)
    allocate_network_bases(job)
    inventory = inventory_for_job(job)
    inventory["status"] = "RUNNING"
    inventory["message"] = "Inventario planificado; scripts en ejecucion."
    inventory["updated_at"] = now_iso()
    commands = script_commands_for_job(job)
    job["script_runner"]["mode"] = "scripts"
    job["script_runner"]["commands"] = [format_command(command) for command in commands]
    job["script_runner"]["vm_inventory"] = inventory
    job["updated_at"] = now_iso()
    write_json(path, job)
    write_vm_inventory(job, inventory, "RUNNING", "Inventario planificado; scripts en ejecucion.")
    log(f"job={job['id']} comandos preparados: {job['script_runner']['commands']}")

    logs = []
    for command_index, command in enumerate(commands):
        log(f"job={job['id']} ejecutando: {format_command(command)}")
        command_log = run_script_command(command, job_run_dir)
        logs.append(command_log)
        if command_log["exit_code"] != 0:
            mark_pending_topology_vms_failed(inventory, command_index)
            job["status"] = "FAILED"
            job["message"] = f"Fallo ejecutando script: {command_log['command']}"
            inventory["status"] = "FAILED"
            inventory["message"] = job["message"]
            inventory["updated_at"] = now_iso()
            job["logs"] = logs
            job["script_runner"]["vm_inventory"] = inventory
            job["updated_at"] = now_iso()
            write_json(path, job)
            write_vm_inventory(job, inventory, "FAILED", job["message"])
            log(f"job={job['id']} FAILED: {job['message']}")
            if command_log.get("output"):
                log(f"job={job['id']} output:\n{command_log['output']}")
            return
        mark_topology_vms(inventory, command_index, "ACTIVE")
        job["script_runner"]["vm_inventory"] = inventory
        write_vm_inventory(job, inventory, "RUNNING", f"Topologia {command_index + 1} procesada.")

    job["status"] = "SUCCESS"
    job["message"] = "Scripts de topologia procesados correctamente."
    inventory["status"] = "SUCCESS"
    inventory["message"] = job["message"]
    inventory["updated_at"] = now_iso()
    job["logs"] = logs
    job["script_runner"]["vm_inventory"] = inventory
    job["updated_at"] = now_iso()
    write_json(path, job)
    write_vm_inventory(job, inventory, "SUCCESS", job["message"])
    log(f"job={job['id']} SUCCESS")


def process_destroy_job(path: Path, job: dict) -> None:
    job_run_dir = RUN_DIR / job["id"]
    job_run_dir.mkdir(parents=True, exist_ok=True)

    script_data = job.setdefault("script_runner", {})
    variables = script_data.setdefault("variables", {})
    inventory = script_data.get("vm_inventory") or variables.get("inventory") or {}
    if not inventory:
        raise ValueError("El job de destruccion no contiene inventario de VMs")

    mark_all_vms(inventory, "DESTROYING")
    mark_all_networks(inventory, "DESTROYING")
    inventory["status"] = "DESTROYING"
    inventory["message"] = "Destruccion de VMs en ejecucion."
    inventory["updated_at"] = now_iso()

    commands = destroy_commands_for_job(job)
    script_data["mode"] = "scripts"
    script_data["commands"] = [format_command(command) for command in commands]
    script_data["vm_inventory"] = inventory
    job["updated_at"] = now_iso()
    write_json(path, job)
    write_vm_inventory(job, inventory, "DESTROYING", "Destruccion de VMs en ejecucion.")
    log(f"job={job['id']} comandos preparados: {job['script_runner']['commands']}")

    logs = []
    for command in commands:
        log(f"job={job['id']} ejecutando: {format_command(command)}")
        command_log = run_script_command(command, job_run_dir)
        logs.append(command_log)
        if command_log["exit_code"] != 0:
            mark_all_vms(inventory, "FAILED")
            mark_all_networks(inventory, "FAILED")
            job["status"] = "FAILED"
            job["message"] = f"Fallo destruyendo VM: {command_log['command']}"
            inventory["status"] = "FAILED"
            inventory["message"] = job["message"]
            inventory["updated_at"] = now_iso()
            job["logs"] = logs
            job["script_runner"]["vm_inventory"] = inventory
            job["updated_at"] = now_iso()
            write_json(path, job)
            write_vm_inventory(job, inventory, "FAILED", job["message"])
            log(f"job={job['id']} FAILED: {job['message']}")
            if command_log.get("output"):
                log(f"job={job['id']} output:\n{command_log['output']}")
            return

    mark_all_vms(inventory, "DESTROYED")
    mark_all_networks(inventory, "DESTROYED")
    job["status"] = "SUCCESS"
    job["message"] = "VMs destruidas correctamente."
    inventory["status"] = "DESTROYED"
    inventory["message"] = job["message"]
    inventory["updated_at"] = now_iso()
    job["logs"] = logs
    job["script_runner"]["vm_inventory"] = inventory
    job["updated_at"] = now_iso()
    write_json(path, job)
    write_vm_inventory(job, inventory, "DESTROYED", job["message"])
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

    action = job.get("script_runner", {}).get("action")
    if action == "destroy_topology":
        process_destroy_job(path, job)
    else:
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
