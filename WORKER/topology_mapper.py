import os
import re
from typing import Any


REMOTE_SCRIPT_DIR = os.getenv("NIMBUSCORE_REMOTE_SCRIPTS_DIR", "/home/ubuntu/script_runner")
HEADNODE_IP = os.getenv("NIMBUSCORE_HEADNODE_IP", "10.0.10.3")
SSH_USER = os.getenv("NIMBUSCORE_SSH_USER", "ubuntu")
SSH_OPTS = os.getenv("NIMBUSCORE_SSH_OPTS", "-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null")
COMPUTE_IPS = os.getenv("NIMBUSCORE_COMPUTE_IPS", "10.0.10.1")
OVS_NAME = os.getenv("NIMBUSCORE_OVS_NAME", "br-int")
VLAN_BASE = int(os.getenv("NIMBUSCORE_VLAN_BASE", "100"))
VNC_BASE = int(os.getenv("NIMBUSCORE_VNC_BASE", "5901"))
CIDR_BASE = int(os.getenv("NIMBUSCORE_CIDR_BASE", "10"))


def safe_name(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9-]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value or "slice"


def script_commands_for_job(job: dict[str, Any]) -> list[list[str]]:
    variables = job.get("script_runner", {}).get("variables", {})
    topologies = variables.get("topologies") or []
    slice_name = safe_name(variables.get("slice_name") or job.get("slice_id") or "slice")

    commands: list[list[str]] = []
    vlan_cursor = VLAN_BASE
    cidr_cursor = CIDR_BASE
    vnc_cursor = VNC_BASE

    for index, topology in enumerate(topologies):
        topo_type = topology.get("type")
        node_count = int(topology.get("count") or 0)
        if not topo_type or node_count <= 0:
            continue

        suffix = f"{index + 1}" if len(topologies) > 1 else ""
        topology_name = safe_name(f"{slice_name}{('-' + suffix) if suffix else ''}")

        if topo_type == "lineal":
            commands.append(remote_headnode_command(
                "create_linear_topology.sh",
                topology_name,
                str(node_count),
                str(vlan_cursor),
                str(vnc_cursor),
                str(cidr_cursor),
            ))
            vlan_cursor += max(node_count - 1, 1)
            cidr_cursor += max(node_count - 1, 1)
            vnc_cursor += node_count
        elif topo_type == "anillo":
            commands.append(remote_headnode_command(
                "create_ring_topology.sh",
                topology_name,
                str(node_count),
                str(vlan_cursor),
                str(vnc_cursor),
                str(cidr_cursor),
            ))
            vlan_cursor += node_count
            cidr_cursor += node_count
            vnc_cursor += node_count
        else:
            raise ValueError(f"Topologia no soportada por scripts: {topo_type}")

    if not commands:
        raise ValueError("El job no tiene topologias ejecutables para SCRIPT_RUNNER")

    return commands


def remote_headnode_command(script_name: str, *args: str) -> list[str]:
    env = (
        "NIMBUSCORE_HEADNODE_LOCAL=true "
        f"NIMBUSCORE_REMOTE_SCRIPTS_DIR={shell_quote(REMOTE_SCRIPT_DIR)} "
        f"NIMBUSCORE_COMPUTE_IPS={shell_quote(COMPUTE_IPS)} "
        f"NIMBUSCORE_OVS_NAME={shell_quote(OVS_NAME)} "
        f"NIMBUSCORE_SSH_USER={shell_quote(SSH_USER)} "
        f"NIMBUSCORE_SSH_OPTS={shell_quote(SSH_OPTS)}"
    )
    remote_command = " ".join(
        [
            env,
            "bash",
            shell_quote(f"{REMOTE_SCRIPT_DIR}/{script_name}"),
            *[shell_quote(arg) for arg in args],
        ]
    )
    return [
        "ssh",
        *SSH_OPTS.split(),
        f"{SSH_USER}@{HEADNODE_IP}",
        remote_command,
    ]


def shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"
