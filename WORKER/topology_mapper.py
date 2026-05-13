import os
import re
import hashlib
from typing import Any


REMOTE_SCRIPT_DIR = os.getenv("NIMBUSCORE_REMOTE_SCRIPTS_DIR", "/home/ubuntu/script_runner")
KEYPAIR_DIR = os.getenv("NIMBUSCORE_KEYPAIR_DIR", "/home/ubuntu/nimbuscore-keys")
HEADNODE_IP = os.getenv("NIMBUSCORE_HEADNODE_IP", "10.0.10.4")
SSH_USER = os.getenv("NIMBUSCORE_SSH_USER", "ubuntu")
SSH_OPTS = os.getenv("NIMBUSCORE_SSH_OPTS", "-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null")
COMPUTE_IPS = os.getenv("NIMBUSCORE_COMPUTE_IPS", "10.0.10.1,10.0.10.2,10.0.10.3")
OVS_NAME = os.getenv("NIMBUSCORE_OVS_NAME", "br-int")
OVS_UPLINKS = os.getenv("NIMBUSCORE_OVS_UPLINKS", "ens4")
CONSOLE_USER = os.getenv("NIMBUSCORE_CONSOLE_USER", "nimbus")
CONSOLE_PASSWORD = os.getenv("NIMBUSCORE_CONSOLE_PASSWORD", "NimbusCore123")
ENABLE_PASSWORD_LOGIN = os.getenv("NIMBUSCORE_ENABLE_PASSWORD_LOGIN", "true")
MAC_SALT = os.getenv("NIMBUSCORE_MAC_SALT", "nimbuscore")
VLAN_BASE = int(os.getenv("NIMBUSCORE_VLAN_BASE", "100"))
DEFAULT_VNC_BASE = int(os.getenv("NIMBUSCORE_VNC_BASE", "5901"))
CIDR_BASE = int(os.getenv("NIMBUSCORE_CIDR_BASE", "10"))
DEFAULT_VM_SPEC = {"vcpus": 1, "ram_mb": 2048, "disk_gb": 20}
DEFAULT_IMAGE = {
    "name": "cirros-0.6.2",
    "url": "https://download.cirros-cloud.net/0.6.2/cirros-0.6.2-x86_64-disk.img",
}
DEFAULT_KEYPAIR = "default-key"


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
    vlan_cursor = int(variables.get("vlan_base") or VLAN_BASE)
    cidr_cursor = int(variables.get("cidr_base") or CIDR_BASE)
    vnc_cursor = int(variables.get("vnc_base") or DEFAULT_VNC_BASE)

    for index, topology in enumerate(topologies):
        topo_type = topology.get("type")
        node_count = int(topology.get("count") or 0)
        if not topo_type or node_count <= 0:
            continue

        suffix = f"{index + 1}" if len(topologies) > 1 else ""
        topology_name = safe_name(f"{slice_name}{('-' + suffix) if suffix else ''}")
        matched_instances = topology_instances(variables, topology)
        vm_specs = topology_vm_specs(matched_instances, node_count)
        image_specs = topology_image_specs(matched_instances, node_count)
        keypair_specs = topology_keypair_specs(matched_instances, node_count)

        if topo_type == "lineal":
            commands.append(remote_headnode_command(
                "create_linear_topology.sh",
                topology_name,
                str(node_count),
                str(vlan_cursor),
                str(vnc_cursor),
                str(cidr_cursor),
                vm_specs=vm_specs,
                image_specs=image_specs,
                keypair_specs=keypair_specs,
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
                vm_specs=vm_specs,
                image_specs=image_specs,
                keypair_specs=keypair_specs,
            ))
            vlan_cursor += node_count
            cidr_cursor += node_count
            vnc_cursor += node_count
        elif topo_type == "personalizada":
            custom_links = topology_custom_links(variables, topology, matched_instances)
            link_specs = topology_link_specs(custom_links)
            commands.append(remote_headnode_command(
                "create_custom_topology.sh",
                topology_name,
                str(node_count),
                str(vlan_cursor),
                str(vnc_cursor),
                str(cidr_cursor),
                vm_specs=vm_specs,
                image_specs=image_specs,
                keypair_specs=keypair_specs,
                link_specs=link_specs,
            ))
            vlan_cursor += len(custom_links)
            cidr_cursor += len(custom_links)
            vnc_cursor += node_count
        else:
            raise ValueError(f"Topologia no soportada por scripts: {topo_type}")

    if not commands:
        raise ValueError("El job no tiene topologias ejecutables para SCRIPT_RUNNER")

    return commands


def destroy_commands_for_job(job: dict[str, Any]) -> list[list[str]]:
    script_data = job.get("script_runner", {})
    variables = script_data.get("variables", {})
    inventory = script_data.get("vm_inventory") or variables.get("inventory") or {}
    vms = inventory.get("vms") or []

    commands: list[list[str]] = []
    for vm in reversed(vms):
        vm_name = vm.get("name") or vm.get("id")
        worker_ip = vm.get("worker_ip")
        ovs_name = vm.get("ovs_name") or OVS_NAME
        if not vm_name or not worker_ip:
            continue
        commands.append(remote_compute_command(
            worker_ip,
            "delete_vm.sh",
            str(vm_name),
            str(ovs_name),
        ))

    seen_vlans: set[int] = set()
    networks = inventory.get("networks") or []
    for network in reversed(networks):
        try:
            vlan_id = int(network.get("vlan_id"))
        except (TypeError, ValueError):
            continue
        if vlan_id in seen_vlans:
            continue
        seen_vlans.add(vlan_id)
        commands.append(remote_headnode_command(
            "delete_network_vlan.sh",
            str(vlan_id),
            str(inventory.get("ovs_name") or OVS_NAME),
        ))

    if not commands:
        raise ValueError("El job de destruccion no tiene VMs ni redes registradas en el inventario")

    return commands


def inventory_for_job(job: dict[str, Any]) -> dict[str, Any]:
    variables = job.get("script_runner", {}).get("variables", {})
    topologies = variables.get("topologies") or []
    slice_name = safe_name(variables.get("slice_name") or job.get("slice_id") or "slice")

    vlan_cursor = int(variables.get("vlan_base") or VLAN_BASE)
    cidr_cursor = int(variables.get("cidr_base") or CIDR_BASE)
    vnc_cursor = int(variables.get("vnc_base") or DEFAULT_VNC_BASE)
    compute_ips = [ip.strip() for ip in COMPUTE_IPS.split(",") if ip.strip()]
    if not compute_ips:
        compute_ips = ["10.0.10.1"]

    inventory: dict[str, Any] = {
        "slice_id": job.get("slice_id"),
        "job_id": job.get("id"),
        "headnode_ip": HEADNODE_IP,
        "ovs_name": OVS_NAME,
        "ovs_uplinks": OVS_UPLINKS,
        "compute_ips": compute_ips,
        "vlan_base": vlan_cursor,
        "cidr_base": cidr_cursor,
        "vnc_base": vnc_cursor,
        "console_user": CONSOLE_USER,
        "vms": [],
        "networks": [],
        "topologies": [],
    }

    for index, topology in enumerate(topologies):
        topo_type = topology.get("type")
        node_count = int(topology.get("count") or 0)
        if not topo_type or node_count <= 0:
            continue

        suffix = f"{index + 1}" if len(topologies) > 1 else ""
        topology_name = safe_name(f"{slice_name}{('-' + suffix) if suffix else ''}")
        matched_instances = topology_instances(variables, topology)
        topology_record = {
            "index": index,
            "id": topology.get("id"),
            "name": topology_name,
            "type": topo_type,
            "node_count": node_count,
            "vlan_base": vlan_cursor,
            "cidr_base": cidr_cursor,
            "vnc_base": vnc_cursor,
        }
        inventory["topologies"].append(topology_record)

        if topo_type == "lineal":
            append_linear_inventory(
                inventory,
                topology_record,
                matched_instances,
                node_count,
                vlan_cursor,
                cidr_cursor,
                vnc_cursor,
                compute_ips,
            )
            vlan_cursor += max(node_count - 1, 1)
            cidr_cursor += max(node_count - 1, 1)
            vnc_cursor += node_count
        elif topo_type == "anillo":
            append_ring_inventory(
                inventory,
                topology_record,
                matched_instances,
                node_count,
                vlan_cursor,
                cidr_cursor,
                vnc_cursor,
                compute_ips,
            )
            vlan_cursor += node_count
            cidr_cursor += node_count
            vnc_cursor += node_count
        elif topo_type == "personalizada":
            custom_links = topology_custom_links(variables, topology, matched_instances)
            append_custom_inventory(
                inventory,
                topology_record,
                matched_instances,
                node_count,
                custom_links,
                vlan_cursor,
                cidr_cursor,
                vnc_cursor,
                compute_ips,
            )
            vlan_cursor += len(custom_links)
            cidr_cursor += len(custom_links)
            vnc_cursor += node_count

    return inventory


def append_linear_inventory(
    inventory: dict[str, Any],
    topology_record: dict[str, Any],
    matched_instances: list[dict[str, Any]],
    node_count: int,
    vlan_base: int,
    cidr_base: int,
    vnc_base: int,
    compute_ips: list[str],
) -> None:
    topology_name = topology_record["name"]
    link_count = max(node_count - 1, 1)

    for link in range(link_count):
        vlan_id = vlan_base + link
        cidr_third = cidr_base + link
        inventory["networks"].append(network_record(
            topology_record,
            link,
            vlan_id,
            cidr_third,
            [f"{topology_name}-vm{link + 1}", f"{topology_name}-vm{link + 2}"],
        ))

    for index in range(node_count):
        vlans = []
        if index < node_count - 1:
            vlans.append(vlan_base + index)
        if index > 0:
            vlans.append(vlan_base + index - 1)
        inventory["vms"].append(vm_record(
            topology_record,
            matched_instances,
            index,
            vnc_base + index,
            compute_ips[index % len(compute_ips)],
            vlans,
            vlan_base,
            cidr_base,
        ))


def append_ring_inventory(
    inventory: dict[str, Any],
    topology_record: dict[str, Any],
    matched_instances: list[dict[str, Any]],
    node_count: int,
    vlan_base: int,
    cidr_base: int,
    vnc_base: int,
    compute_ips: list[str],
) -> None:
    topology_name = topology_record["name"]

    for link in range(node_count):
        vlan_id = vlan_base + link
        cidr_third = cidr_base + link
        vm_a = link + 1
        vm_b = (link + 1) % node_count + 1
        inventory["networks"].append(network_record(
            topology_record,
            link,
            vlan_id,
            cidr_third,
            [f"{topology_name}-vm{vm_a}", f"{topology_name}-vm{vm_b}"],
        ))

    for index in range(node_count):
        right_vlan = vlan_base + (index % node_count)
        left_vlan = vlan_base + ((index - 1 + node_count) % node_count)
        inventory["vms"].append(vm_record(
            topology_record,
            matched_instances,
            index,
            vnc_base + index,
            compute_ips[index % len(compute_ips)],
            [right_vlan, left_vlan],
            vlan_base,
            cidr_base,
        ))


def append_custom_inventory(
    inventory: dict[str, Any],
    topology_record: dict[str, Any],
    matched_instances: list[dict[str, Any]],
    node_count: int,
    custom_links: list[dict[str, int]],
    vlan_base: int,
    cidr_base: int,
    vnc_base: int,
    compute_ips: list[str],
) -> None:
    topology_name = topology_record["name"]
    vm_vlans: list[list[int]] = [[] for _ in range(node_count)]

    for link_index, link in enumerate(custom_links):
        vlan_id = vlan_base + link_index
        cidr_third = cidr_base + link_index
        from_index = int(link["from_index"])
        to_index = int(link["to_index"])
        vm_vlans[from_index].append(vlan_id)
        vm_vlans[to_index].append(vlan_id)
        inventory["networks"].append(network_record(
            topology_record,
            link_index,
            vlan_id,
            cidr_third,
            [
                f"{topology_name}-vm{from_index + 1}",
                f"{topology_name}-vm{to_index + 1}",
            ],
        ))

    for index in range(node_count):
        inventory["vms"].append(vm_record(
            topology_record,
            matched_instances,
            index,
            vnc_base + index,
            compute_ips[index % len(compute_ips)],
            vm_vlans[index],
            vlan_base,
            cidr_base,
        ))


def network_record(
    topology_record: dict[str, Any],
    link_index: int,
    vlan_id: int,
    cidr_third: int,
    vm_names: list[str],
) -> dict[str, Any]:
    return {
        "topology_index": topology_record["index"],
        "topology_id": topology_record.get("id"),
        "topology_name": topology_record["name"],
        "link_index": link_index,
        "vlan_id": vlan_id,
        "cidr": f"192.168.{cidr_third}.0/24",
        "gateway": f"192.168.{cidr_third}.1",
        "dhcp_start": f"192.168.{cidr_third}.10",
        "dhcp_end": f"192.168.{cidr_third}.100",
        "dhcp_namespace": f"dhcp-ns-vlan{vlan_id}",
        "dhcp_host_interface": f"veth-h-{vlan_id}",
        "dhcp_namespace_interface": f"veth-ns-{vlan_id}",
        "connected_vms": vm_names,
    }


def vm_record(
    topology_record: dict[str, Any],
    matched_instances: list[dict[str, Any]],
    index: int,
    vnc_port: int,
    worker_ip: str,
    vlans: list[int],
    vlan_base: int,
    cidr_base: int,
) -> dict[str, Any]:
    vm_index = index + 1
    vm_name = f"{topology_record['name']}-vm{vm_index}"
    instance = matched_instances[index] if index < len(matched_instances) else {}
    flavor = instance.get("flavor") or "m1.small"
    vcpus = int(instance.get("vcpus") or DEFAULT_VM_SPEC["vcpus"])
    ram_mb = int(instance.get("ram_mb") or DEFAULT_VM_SPEC["ram_mb"])
    disk_gb = int(instance.get("disk_gb") or DEFAULT_VM_SPEC["disk_gb"])
    image_name = safe_image_name(instance.get("image_name") or DEFAULT_IMAGE["name"])
    image_url = instance.get("image_url") or DEFAULT_IMAGE["url"]
    image_download_method = instance.get("image_download_method") or "auto"
    key_pair = safe_keypair_name(instance.get("key_pair") or DEFAULT_KEYPAIR)

    nics = []
    for iface_index, vlan_id in enumerate(vlans):
        cidr_third = cidr_base + (vlan_id - vlan_base)
        nics.append({
            "index": iface_index,
            "name": f"eth{iface_index}",
            "vlan_id": vlan_id,
            "cidr": f"192.168.{cidr_third}.0/24",
            "gateway": f"192.168.{cidr_third}.1",
            "dhcp_range": {
                "start": f"192.168.{cidr_third}.10",
                "end": f"192.168.{cidr_third}.100",
            },
            "dhcp_namespace": f"dhcp-ns-vlan{vlan_id}",
            "mac": mac_for_iface(vm_name, iface_index, vlan_id),
            "tap": tap_for_iface(vm_name, iface_index, vlan_id),
        })

    return {
        "id": vm_name,
        "name": vm_name,
        "status": "PLANNED",
        "topology_index": topology_record["index"],
        "topology_id": topology_record.get("id"),
        "topology_name": topology_record["name"],
        "topology_type": topology_record["type"],
        "vm_index": vm_index,
        "node_id": instance.get("node_id"),
        "node_type": instance.get("node_type", "srv"),
        "worker_ip": worker_ip,
        "vnc_port": vnc_port,
        "vnc_display": vnc_port - 5900,
        "vnc_target": f"{worker_ip}:{vnc_port}",
        "ovs_name": OVS_NAME,
        "vlans": vlans,
        "nics": nics,
        "flavor": flavor,
        "vcpus": vcpus,
        "ram_mb": ram_mb,
        "disk_gb": disk_gb,
        "image": instance.get("image"),
        "image_name": image_name,
        "image_url": image_url,
        "image_download_method": image_download_method,
        "key_pair": key_pair,
        "security_ports": instance.get("security_ports") or [],
        "custom_rules": instance.get("custom_rules") or [],
        "console_user": CONSOLE_USER,
    }


def mac_for_iface(vm_name: str, iface_idx: int, vlan_id: int) -> str:
    digest = hashlib.sha1(f"{MAC_SALT}:{vm_name}:{iface_idx}:{vlan_id}".encode()).hexdigest()
    return f"52:54:00:{digest[0:2]}:{digest[2:4]}:{digest[4:6]}"


def tap_for_iface(vm_name: str, iface_idx: int, vlan_id: int) -> str:
    digest = hashlib.sha1(f"{vm_name}-{iface_idx}-{vlan_id}".encode()).hexdigest()
    return f"tap{digest[:10]}"


def topology_instances(variables: dict[str, Any], topology: dict[str, Any]) -> list[dict[str, Any]]:
    topology_id = topology.get("id")
    instances = variables.get("instances") or []
    if topology_id is None:
        return []

    matched = [
        instance for instance in instances
        if str(instance.get("topology_id")) == str(topology_id)
    ]
    matched.sort(key=lambda item: int(item.get("topology_node_index") or 0))
    return matched


def topology_custom_links(
    variables: dict[str, Any],
    topology: dict[str, Any],
    matched_instances: list[dict[str, Any]],
) -> list[dict[str, int]]:
    node_to_index = {
        str(instance.get("node_id")): index
        for index, instance in enumerate(matched_instances)
        if instance.get("node_id") is not None
    }
    if not node_to_index:
        return []

    custom_links: list[dict[str, int]] = []
    seen: set[tuple[int, int]] = set()
    links = variables.get("links") or []
    topology_id = topology.get("id")

    for link in links:
        if link.get("tipo") == "bus":
            continue

        source = link.get("desde") or link.get("from")
        target = link.get("hacia") or link.get("to")
        if source is None or target is None:
            continue

        source_key = str(source)
        target_key = str(target)
        if source_key not in node_to_index or target_key not in node_to_index:
            continue

        if topology_id is not None:
            link_topology = link.get("topologia_id") or link.get("topology_id")
            if link_topology is not None and str(link_topology) != str(topology_id):
                continue

        from_index = node_to_index[source_key]
        to_index = node_to_index[target_key]
        if from_index == to_index:
            continue

        dedupe_key = tuple(sorted((from_index, to_index)))
        if dedupe_key in seen:
            continue

        seen.add(dedupe_key)
        custom_links.append({
            "from_index": from_index,
            "to_index": to_index,
        })

    return custom_links


def topology_link_specs(custom_links: list[dict[str, int]]) -> str:
    return ";".join(
        f"{link['from_index']}-{link['to_index']}"
        for link in custom_links
    )


def topology_vm_specs(matched: list[dict[str, Any]], node_count: int) -> str:
    specs = []

    for index in range(node_count):
        instance = matched[index] if index < len(matched) else {}
        vcpus = int(instance.get("vcpus") or DEFAULT_VM_SPEC["vcpus"])
        ram_mb = int(instance.get("ram_mb") or DEFAULT_VM_SPEC["ram_mb"])
        disk_gb = int(instance.get("disk_gb") or DEFAULT_VM_SPEC["disk_gb"])
        specs.append(f"{vcpus}:{ram_mb}:{disk_gb}")

    return ";".join(specs)


def topology_image_specs(matched: list[dict[str, Any]], node_count: int) -> str:
    specs = []

    for index in range(node_count):
        instance = matched[index] if index < len(matched) else {}
        image_name = safe_image_name(instance.get("image_name") or DEFAULT_IMAGE["name"])
        image_url = instance.get("image_url") or DEFAULT_IMAGE["url"]
        download_method = instance.get("image_download_method") or "auto"
        specs.append(f"{image_name}|{image_url}|{download_method}")

    return ";".join(specs)


def topology_keypair_specs(matched: list[dict[str, Any]], node_count: int) -> str:
    specs = []

    for index in range(node_count):
        instance = matched[index] if index < len(matched) else {}
        keypair = safe_keypair_name(instance.get("key_pair") or DEFAULT_KEYPAIR)
        specs.append(keypair)

    return ";".join(specs)


def safe_image_name(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9._-]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-._")
    return value or "image"


def safe_keypair_name(value: str) -> str:
    return safe_name(value)


def remote_headnode_command(
    script_name: str,
    *args: str,
    vm_specs: str = "",
    image_specs: str = "",
    keypair_specs: str = "",
    link_specs: str = "",
) -> list[str]:
    env = (
        "NIMBUSCORE_HEADNODE_LOCAL=true "
        f"NIMBUSCORE_REMOTE_SCRIPTS_DIR={shell_quote(REMOTE_SCRIPT_DIR)} "
        f"NIMBUSCORE_KEYPAIR_DIR={shell_quote(KEYPAIR_DIR)} "
        f"NIMBUSCORE_COMPUTE_IPS={shell_quote(COMPUTE_IPS)} "
        f"NIMBUSCORE_OVS_NAME={shell_quote(OVS_NAME)} "
        f"NIMBUSCORE_OVS_UPLINKS={shell_quote(OVS_UPLINKS)} "
        f"NIMBUSCORE_SSH_USER={shell_quote(SSH_USER)} "
        f"NIMBUSCORE_SSH_OPTS={shell_quote(SSH_OPTS)} "
        f"NIMBUSCORE_CONSOLE_USER={shell_quote(CONSOLE_USER)} "
        f"NIMBUSCORE_CONSOLE_PASSWORD={shell_quote(CONSOLE_PASSWORD)} "
        f"NIMBUSCORE_ENABLE_PASSWORD_LOGIN={shell_quote(ENABLE_PASSWORD_LOGIN)} "
        f"NIMBUSCORE_TOPOLOGY_VM_SPECS={shell_quote(vm_specs)} "
        f"NIMBUSCORE_TOPOLOGY_IMAGE_SPECS={shell_quote(image_specs)} "
        f"NIMBUSCORE_TOPOLOGY_KEYPAIR_SPECS={shell_quote(keypair_specs)} "
        f"NIMBUSCORE_TOPOLOGY_LINK_SPECS={shell_quote(link_specs)}"
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


def remote_compute_command(
    worker_ip: str,
    script_name: str,
    *args: str,
) -> list[str]:
    env = (
        f"NIMBUSCORE_REMOTE_SCRIPTS_DIR={shell_quote(REMOTE_SCRIPT_DIR)} "
        f"NIMBUSCORE_OVS_NAME={shell_quote(OVS_NAME)}"
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
        f"{SSH_USER}@{worker_ip}",
        remote_command,
    ]


def shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"
