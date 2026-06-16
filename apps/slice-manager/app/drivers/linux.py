from copy import deepcopy
from typing import Any

from app.drivers.base import ClusterDriver, DeployJobRequest, DestroyJobRequest
from app.services.script_payload import build_script_variables
from app.services.vm_placement import place_vms


class LinuxClusterDriver(ClusterDriver):
    name = "linux"
    implemented = True

    def build_deploy_job(self, request: DeployJobRequest) -> dict[str, Any]:
        slice_item = request.slice_item
        placements = place_vms(slice_item)
        script_variables = build_script_variables(slice_item, placements)
        return {
            "id": request.job_id,
            "slice_id": slice_item["id"],
            "driver": self.name,
            "availability_zone": self.zone_id,
            "status": "QUEUED",
            "message": "Job creado. El driver Linux ejecutara Script Runner.",
            "placements": placements,
            "script_runner": {
                "driver": self.name,
                "action": "create_topology",
                "variables": script_variables,
            },
            "created_at": request.created_at,
            "updated_at": request.created_at,
        }

    def build_destroy_job(self, request: DestroyJobRequest) -> dict[str, Any]:
        return {
            "id": request.job_id,
            "slice_id": request.slice_item["id"],
            "driver": self.name,
            "availability_zone": self.zone_id,
            "status": "QUEUED",
            "message": "Job creado. El driver Linux destruira las VMs registradas.",
            "script_runner": {
                "driver": self.name,
                "action": "destroy_topology",
                "variables": {
                    "inventory": deepcopy(request.inventory),
                },
                "vm_inventory": deepcopy(request.inventory),
            },
            "created_at": request.created_at,
            "updated_at": request.created_at,
        }


def linux_driver_contract() -> dict[str, Any]:
    return {
        "driver": LinuxClusterDriver.name,
        "job_transport": "json-file-queue",
        "queue_consumer": "drivers/linux/worker",
        "script_runner": True,
        "actions": ["create_topology", "destroy_topology"],
    }
