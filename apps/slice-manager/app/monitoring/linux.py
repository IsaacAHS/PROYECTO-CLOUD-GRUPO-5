import math
import os
import shlex
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from app.monitoring.models import (
    ClusterMetricsSnapshot,
    HostAllocations,
    HostCapacity,
    HostMetrics,
    HostRealUsage,
)
from app.monitoring.service import default_overbooking_policy, empty_host_metrics
from app.services.vm_inventory_store import load_inventory_store


REMOTE_METRICS_SCRIPT = r"""
set -eu
hostname_value="$(hostname 2>/dev/null || echo unknown)"
vcpus_total="$(nproc 2>/dev/null || echo 0)"
mem_total_kb="$(awk '/^MemTotal:/ {print $2}' /proc/meminfo 2>/dev/null || echo 0)"
mem_available_kb="$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo 2>/dev/null || echo 0)"
cpu_a="$(awk '/^cpu / {for (i=2; i<=NF; i++) printf "%s%s", $i, (i<NF ? " " : "\n")}' /proc/stat 2>/dev/null || echo '')"
sleep "${NIMBUSCORE_MONITOR_CPU_SAMPLE_SECONDS:-0.4}"
cpu_b="$(awk '/^cpu / {for (i=2; i<=NF; i++) printf "%s%s", $i, (i<NF ? " " : "\n")}' /proc/stat 2>/dev/null || echo '')"
df_line="$(df -Pm /var/lib/qemu/images 2>/dev/null | awk 'NR==2 {print $2" "$3" "$5}' || true)"
if [ -z "$df_line" ]; then
  df_line="$(df -Pm / 2>/dev/null | awk 'NR==2 {print $2" "$3" "$5}' || true)"
fi
qemu_processes="$(pgrep -fc 'qemu-system' 2>/dev/null || true)"
printf 'hostname=%s\n' "$hostname_value"
printf 'vcpus_total=%s\n' "$vcpus_total"
printf 'mem_total_kb=%s\n' "$mem_total_kb"
printf 'mem_available_kb=%s\n' "$mem_available_kb"
printf 'cpu_a=%s\n' "$cpu_a"
printf 'cpu_b=%s\n' "$cpu_b"
printf 'df_line=%s\n' "$df_line"
printf 'qemu_processes=%s\n' "${qemu_processes:-0}"
"""


EXCLUDED_VM_STATUSES = {"DESTROYED", "FAILED"}
RUNNING_VM_STATUSES = {"ACTIVE", "RUNNING", "DESTROYING"}


def int_value(value: Any, default: int = 0) -> int:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default


def float_value(value: Any, default: float | None = None) -> float | None:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def parse_key_values(output: str) -> dict[str, str]:
    values = {}
    for line in output.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            values[key.strip()] = value.strip()
    return values


def cpu_used_percent(first_sample: str, second_sample: str) -> float | None:
    first = [int_value(item) for item in first_sample.split()]
    second = [int_value(item) for item in second_sample.split()]
    if len(first) < 5 or len(second) < 5 or len(first) != len(second):
        return None

    first_idle = first[3] + first[4]
    second_idle = second[3] + second[4]
    first_total = sum(first)
    second_total = sum(second)
    total_delta = second_total - first_total
    idle_delta = second_idle - first_idle
    if total_delta <= 0:
        return None
    return max(min((1 - (idle_delta / total_delta)) * 100, 100), 0)


def parse_df_line(value: str) -> tuple[int | None, float | None]:
    parts = value.split()
    if len(parts) < 3:
        return None, None
    total_mb = int_value(parts[0], 0)
    used_percent = float_value(parts[2].rstrip("%"))
    total_gb = math.ceil(total_mb / 1024) if total_mb > 0 else None
    return total_gb, used_percent


def allocations_by_host() -> dict[str, HostAllocations]:
    counters: dict[str, dict[str, int]] = {}
    store = load_inventory_store()

    for inventory in store.get("slices", {}).values():
        if not isinstance(inventory, dict):
            continue
        for vm in inventory.get("vms", []) or []:
            if not isinstance(vm, dict):
                continue

            status = str(vm.get("status") or "").upper()
            if status in EXCLUDED_VM_STATUSES:
                continue

            host = str(vm.get("worker_ip") or vm.get("host") or "").strip()
            if not host:
                continue

            host_counter = counters.setdefault(
                host,
                {
                    "allocated_vcpus": 0,
                    "allocated_ram_mb": 0,
                    "allocated_disk_gb": 0,
                    "running_vms": 0,
                    "planned_vms": 0,
                },
            )
            host_counter["allocated_vcpus"] += int_value(vm.get("vcpus"), 0)
            host_counter["allocated_ram_mb"] += int_value(vm.get("ram_mb"), 0)
            host_counter["allocated_disk_gb"] += int_value(vm.get("disk_gb"), 0)
            if status in RUNNING_VM_STATUSES:
                host_counter["running_vms"] += 1
            else:
                host_counter["planned_vms"] += 1

    return {host: HostAllocations(**values) for host, values in counters.items()}


class LinuxMonitor:
    def __init__(self) -> None:
        self.ssh_user = os.getenv("NIMBUSCORE_SSH_USER", "ubuntu")
        self.ssh_opts = os.getenv(
            "NIMBUSCORE_SSH_OPTS",
            "-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null",
        )
        self.ssh_timeout = int_value(os.getenv("NIMBUSCORE_MONITOR_SSH_TIMEOUT"), 8)
        self.max_workers = max(int_value(os.getenv("NIMBUSCORE_MONITOR_MAX_WORKERS"), 4), 1)

    def ssh_command(self, host: str) -> list[str]:
        return [
            "ssh",
            *shlex.split(self.ssh_opts),
            "-o",
            "BatchMode=yes",
            "-o",
            f"ConnectTimeout={self.ssh_timeout}",
            f"{self.ssh_user}@{host}",
            "sh",
            "-s",
        ]

    def collect_host(
        self,
        zone: dict[str, Any],
        host: str,
        allocations: dict[str, HostAllocations],
    ) -> HostMetrics:
        result = subprocess.run(
            self.ssh_command(host),
            input=REMOTE_METRICS_SCRIPT,
            check=False,
            capture_output=True,
            text=True,
            timeout=self.ssh_timeout + 3,
        )
        if result.returncode != 0:
            message = (result.stderr or result.stdout or "SSH sin respuesta").strip()
            fallback = empty_host_metrics(zone, host)
            return HostMetrics(
                host_id=fallback.host_id,
                hostname=fallback.hostname,
                zone=fallback.zone,
                driver=fallback.driver,
                status="offline",
                allocations=allocations.get(host, HostAllocations()),
                policy=default_overbooking_policy(),
                source="linux-ssh",
                errors=[message],
            )

        data = parse_key_values(result.stdout)
        mem_total_mb = int_value(data.get("mem_total_kb"), 0) // 1024
        mem_available_mb = int_value(data.get("mem_available_kb"), 0) // 1024
        mem_used_mb = max(mem_total_mb - mem_available_mb, 0) if mem_total_mb else None
        mem_used_percent = (
            (mem_used_mb / mem_total_mb) * 100
            if mem_used_mb is not None and mem_total_mb
            else None
        )
        disk_total_gb, disk_used_percent = parse_df_line(data.get("df_line", ""))

        return HostMetrics(
            host_id=host,
            hostname=data.get("hostname") or host,
            zone=str(zone["id"]),
            driver=str(zone["driver"]),
            status="online",
            capacity=HostCapacity(
                vcpus_total=int_value(data.get("vcpus_total"), None),
                ram_total_mb=mem_total_mb or None,
                disk_total_gb=disk_total_gb,
            ),
            real_usage=HostRealUsage(
                cpu_used_percent=cpu_used_percent(data.get("cpu_a", ""), data.get("cpu_b", "")),
                ram_used_mb=mem_used_mb,
                ram_available_mb=mem_available_mb or None,
                ram_used_percent=mem_used_percent,
                disk_used_percent=disk_used_percent,
                qemu_processes=int_value(data.get("qemu_processes"), 0),
            ),
            allocations=allocations.get(host, HostAllocations()),
            policy=default_overbooking_policy(),
            source="linux-ssh",
        )

    def snapshot(self, zone: dict[str, Any]) -> ClusterMetricsSnapshot:
        allocations = allocations_by_host()
        hosts = [str(host) for host in zone.get("hosts", [])]
        metrics: list[HostMetrics] = []

        with ThreadPoolExecutor(max_workers=min(self.max_workers, max(len(hosts), 1))) as executor:
            futures = {
                executor.submit(self.collect_host, zone, host, allocations): host
                for host in hosts
            }
            for future in as_completed(futures):
                host = futures[future]
                try:
                    metrics.append(future.result())
                except Exception as exc:
                    fallback = empty_host_metrics(zone, host)
                    metrics.append(
                        HostMetrics(
                            host_id=fallback.host_id,
                            hostname=fallback.hostname,
                            zone=fallback.zone,
                            driver=fallback.driver,
                            status="offline",
                            allocations=allocations.get(host, HostAllocations()),
                            policy=default_overbooking_policy(),
                            source="linux-ssh",
                            errors=[str(exc)],
                        )
                    )

        metrics.sort(key=lambda item: item.host_id)
        return ClusterMetricsSnapshot(hosts=metrics, source="linux-ssh")
