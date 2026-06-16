from app.drivers.base import ClusterDriver


class OpenStackClusterDriver(ClusterDriver):
    name = "openstack"
    implemented = False

    def not_implemented_message(self) -> str:
        return (
            f"La zona {self.label} esta registrada, "
            "pero el driver OpenStack todavia no esta implementado."
        )
