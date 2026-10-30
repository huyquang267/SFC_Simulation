from dataclasses import dataclass

import networkx as nx

from config import CNF_ORDER, CPU_EDGE, CPU_CLOUD
from topology import build_topology
from workload import SFCRequest


# Mbps truyền giữa hai CNF liên tiếp.
CHAIN_BANDWIDTH = {
    ("Cap", "Dec"): 47.35,
    ("Dec", "Den"): 16.32,
    ("Den", "LoS"): 0.6,
}

EPSILON = 1e-9


@dataclass
class Allocation:
    request: SFCRequest
    placement: dict
    paths: dict
    cpu_reserved: dict
    bandwidth_reserved: dict


class ResourceManager:
    def __init__(self, graph, edges, servers):
        self.graph = graph
        self.edges = edges
        self.servers = servers
        self.devices = {**edges, **servers}

        # request_id -> Allocation
        self.active = {}

        # Một camera chỉ có một SFC đang hoạt động.
        self.active_cameras = set()

    @staticmethod
    def link_key(source, target):
        # Graph hiện tại là vô hướng:
        # A-B và B-A dùng chung tài nguyên.
        return tuple(sorted((source, target)))

    def embed(self, request, placement):
        """
        Thử cấp tài nguyên theo placement đã cho.

        Thành công: trả về (Allocation, "accepted").
        Thất bại: trả về (None, lý do).

        Nếu thất bại, tài nguyên của mạng không thay đổi.
        """
        if request.request_id in self.active:
            return None, "request_already_active"

        if request.camera_id in self.active_cameras:
            return None, "camera_already_active"

        if request.source_device not in self.edges:
            return None, "invalid_source_device"

        if set(placement) != set(CNF_ORDER):
            return None, "placement_must_include_all_four_cnfs"

        if placement["Cap"] != request.source_device:
            return None, "Cap_must_stay_on_source_edge"

        # Tính CPU cần cấp, chưa thay đổi CPU của thiết bị.
        cpu_reserved = {}

        for cnf in CNF_ORDER:
            device_id = placement[cnf]

            if device_id not in self.devices:
                return None, f"unknown_device: {device_id}"

            if device_id in self.edges:
                demand = CPU_EDGE[cnf]
            else:
                demand = CPU_CLOUD[cnf]

            if demand is None:
                return None, f"{cnf}_cannot_run_on_cloud"

            cpu_reserved[device_id] = (
                cpu_reserved.get(device_id, 0.0) + demand
            )

        for device_id, demand in cpu_reserved.items():
            device = self.devices[device_id]

            if device.cpu_used + demand > (
                device.cpu_capacity + EPSILON
            ):
                return None, f"insufficient_cpu: {device_id}"

        # Tìm đường và tính băng thông cần cấp.
        # bandwidth_reserved còn là bản kế hoạch tạm thời.
        paths = {}
        bandwidth_reserved = {}

        for source_cnf, target_cnf in zip(
            CNF_ORDER[:-1], CNF_ORDER[1:]
        ):
            source = placement[source_cnf]
            target = placement[target_cnf]
            pair = (source_cnf, target_cnf)

            if source == target:
                paths[pair] = [source]
                continue

            demand = CHAIN_BANDWIDTH[pair]

            def link_has_capacity(u, v):
                key = self.link_key(u, v)
                data = self.graph[u][v]

                remaining = (
                    data["bandwidth_capacity"]
                    - data["bandwidth_used"]
                    - bandwidth_reserved.get(key, 0.0)
                )

                return remaining + EPSILON >= demand

            # Chỉ giữ những link đủ băng thông cho kết nối này.
            feasible_graph = nx.subgraph_view(
                self.graph,
                filter_edge=link_has_capacity,
            )

            try:
                # Graph không có trọng số:
                # tìm đường có số hop ít nhất.
                path = nx.shortest_path(
                    feasible_graph,
                    source=source,
                    target=target,
                )
            except (nx.NetworkXNoPath, nx.NodeNotFound):
                return None, (
                    f"no_feasible_path: "
                    f"{source_cnf}->{target_cnf}"
                )

            paths[pair] = path

            for u, v in zip(path[:-1], path[1:]):
                key = self.link_key(u, v)

                bandwidth_reserved[key] = (
                    bandwidth_reserved.get(key, 0.0)
                    + demand
                )

        # Đến đây CPU và tất cả đường truyền đều đủ.
        # Bây giờ mới thực sự cấp tài nguyên.
        for device_id, demand in cpu_reserved.items():
            self.devices[device_id].cpu_used += demand

        for (u, v), demand in bandwidth_reserved.items():
            self.graph[u][v]["bandwidth_used"] += demand

        allocation = Allocation(
            request=request,
            placement=dict(placement),
            paths=paths,
            cpu_reserved=cpu_reserved,
            bandwidth_reserved=bandwidth_reserved,
        )

        self.active[request.request_id] = allocation
        self.active_cameras.add(request.camera_id)

        return allocation, "accepted"

    def release(self, request_id):
        """Trả lại đúng tài nguyên đã cấp cho SFC."""
        allocation = self.active.pop(request_id, None)

        if allocation is None:
            return False

        for device_id, demand in (
            allocation.cpu_reserved.items()
        ):
            device = self.devices[device_id]
            device.cpu_used = max(
                0.0,
                device.cpu_used - demand,
            )

        for (u, v), demand in (
            allocation.bandwidth_reserved.items()
        ):
            data = self.graph[u][v]
            data["bandwidth_used"] = max(
                0.0,
                data["bandwidth_used"] - demand,
            )

        self.active_cameras.remove(
            allocation.request.camera_id
        )

        return True


def main():
    graph, edges, servers, cameras = build_topology()

    manager = ResourceManager(graph, edges, servers)

    # Chọn một camera để kiểm tra.
    camera_id = sorted(cameras)[0]
    source_device = cameras[camera_id]

    request = SFCRequest(
        request_id=0,
        camera_id=camera_id,
        source_device=source_device,
        arrival_time=0.0,
        service_time=3600.0,
    )

    # Kiểm tra cách đặt của phương án Cloud.
    # Đây là placement cho ví dụ kiểm tra,
    # chưa phải thuật toán tự chọn server.
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

    print("Result:", reason)

    if allocation is None:
        return

    print("\nPlacement:")
    for cnf in CNF_ORDER:
        print(f"  {cnf}: {allocation.placement[cnf]}")

    print("\nCPU after allocation:")
    print(
        f"  {source_device}: "
        f"{edges[source_device].cpu_used:.2f}%"
    )
    print(
        f"  server_0: "
        f"{servers['server_0'].cpu_used:.2f}%"
    )

    print("\nPaths:")
    for pair, path in allocation.paths.items():
        label = f"{pair[0]} -> {pair[1]}"
        print(f"  {label}: {' -> '.join(path)}")

    print("\nReserved bandwidth on physical links:")
    for (u, v), demand in (
        allocation.bandwidth_reserved.items()
    ):
        print(f"  {u} -- {v}: {demand:.2f} Mbps")

    manager.release(request.request_id)

    print("\nAfter release:")
    print(
        f"  {source_device} CPU: "
        f"{edges[source_device].cpu_used:.2f}%"
    )
    print(
        f"  server_0 CPU: "
        f"{servers['server_0'].cpu_used:.2f}%"
    )

    total_bandwidth = sum(
        data["bandwidth_used"]
        for _, _, data in graph.edges(data=True)
    )

    print(
        f"  Sum of bandwidth used across links: "
        f"{total_bandwidth:.2f} Mbps"
    )
    print(f"  Active SFCs: {len(manager.active)}")


if __name__ == "__main__":
    main()