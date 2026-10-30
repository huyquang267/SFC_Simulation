from topology import build_topology
from workload import SFCRequest
from embedding import ResourceManager


# Nguồn: tài liệu [11], Table III.
EDGE_BASE_POWER_W = 1.21
CLOUD_BASE_POWER_W = 205.10

EDGE_CNF_POWER_W = {
    "Cap": 0.43,  # Đã bao gồm camera.
    "Dec": 0.09,
    "Den": 0.11,
    "LoS": None,  # Chưa có tham số xác minh.
}

CLOUD_CNF_POWER_W = {
    "Cap": None,
    "Dec": 1.67,
    "Den": 7.23,
    "LoS": 5.52,
}


def computing_power(manager):
    """
    Tính công suất của các máy tính đang hoạt động.

    Chưa bao gồm:
    - Switch và port mạng.
    - Năng lượng tái cấu hình.

    Máy không có CNF được xem là tắt trong phép tính này.
    """
    active_edges = set()
    active_servers = set()

    edge_cnf_power = 0.0
    cloud_cnf_power = 0.0

    for allocation in manager.active.values():
        for cnf, device_id in (
            allocation.placement.items()
        ):
            if device_id in manager.edges:
                power = EDGE_CNF_POWER_W[cnf]

                if power is None:
                    raise ValueError(
                        f"Chưa có công suất {cnf} ở edge. "
                        "Cần tham số từ nguồn gốc "
                        "trước khi tính kết quả kW."
                    )

                active_edges.add(device_id)
                edge_cnf_power += power

            elif device_id in manager.servers:
                power = CLOUD_CNF_POWER_W[cnf]

                if power is None:
                    raise ValueError(
                        f"Không có công suất {cnf} ở cloud."
                    )

                active_servers.add(device_id)
                cloud_cnf_power += power

            else:
                raise ValueError(
                    f"Thiết bị không hợp lệ: {device_id}"
                )

    edge_power = (
        len(active_edges) * EDGE_BASE_POWER_W
        + edge_cnf_power
    )

    cloud_power = (
        len(active_servers) * CLOUD_BASE_POWER_W
        + cloud_cnf_power
    )

    return {
        "active_edges": len(active_edges),
        "active_servers": len(active_servers),
        "edge_power_w": edge_power,
        "cloud_power_w": cloud_power,
        "computing_power_w": edge_power + cloud_power,
    }


def energy_j(power_w, duration_s):
    """W × giây = Joule."""
    if power_w < 0 or duration_s < 0:
        raise ValueError(
            "Công suất và thời gian phải không âm."
        )

    return power_w * duration_s


def average_power_kw(total_energy_j, duration_s):
    """Đổi tổng năng lượng thành công suất trung bình kW."""
    if duration_s <= 0:
        raise ValueError(
            "Thời gian đo phải lớn hơn 0."
        )

    return total_energy_j / duration_s / 1000.0


def main():
    graph, edges, servers, cameras = build_topology()

    manager = ResourceManager(
        graph,
        edges,
        servers,
    )

    source_device = "edge_0_0"

    camera_id = next(
        camera_id
        for camera_id, device_id in cameras.items()
        if device_id == source_device
    )

    request = SFCRequest(
        request_id=0,
        camera_id=camera_id,
        source_device=source_device,
        arrival_time=0.0,
        service_time=3600.0,
    )

    placement = {
        "Cap": source_device,
        "Dec": "server_0",
        "Den": "server_0",
        "LoS": "server_0",
    }

    allocation, reason = manager.embed(
        request,
        placement,
    )

    if allocation is None:
        raise RuntimeError(reason)

    power = computing_power(manager)

    print("Active edges:", power["active_edges"])
    print("Active servers:", power["active_servers"])

    print(
        f"Edge computing power: "
        f"{power['edge_power_w']:.2f} W"
    )

    print(
        f"Cloud computing power: "
        f"{power['cloud_power_w']:.2f} W"
    )

    total_power = power["computing_power_w"]

    print(
        f"Total computing power: "
        f"{total_power:.2f} W"
    )

    duration = 3600.0
    total_energy = energy_j(total_power, duration)

    print(
        f"Computing energy over 1 hour: "
        f"{total_energy:.2f} J"
    )

    print(
        f"Average computing power: "
        f"{average_power_kw(total_energy, duration):.5f} kW"
    )


if __name__ == "__main__":
    main()