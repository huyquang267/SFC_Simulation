from dataclasses import dataclass

import networkx as nx

from config import (
    EDGE_CLUSTERS,
    DEVICES_PER_CLUSTER,
    CAMERAS_PER_DEVICE,
    EDGE_INGRESS,
    CLOUD_SERVERS,
    CLOUD_INGRESS,
    EDGE_LINK_MBPS,
    CORE_LINK_MBPS,
    DC_LINK_MBPS,
    FAT_TREE_K,
)


# Các liên kết backbone đọc từ Hình 4
ATLANTA_LINKS = [
    ("N1", "N6"),
    ("N1", "N7"),
    ("N1", "N8"),
    ("N2", "N3"),
    ("N2", "N5"),
    ("N2", "N6"),
    ("N3", "N5"),
    ("N3", "N8"),
    ("N4", "N5"),
    ("N4", "N6"),
    ("N6", "N13"),
    ("N7", "N10"),
    ("N7", "N14"),
    ("N8", "N9"),
    ("N8", "N15"),
    ("N9", "N10"),
    ("N9", "N12"),
    ("N9", "N15"),
    ("N10", "N12"),
    ("N11", "N13"),
    ("N11", "N14"),
    ("N13", "N14"),
]


@dataclass
class Device:
    device_id: str
    tier: str
    ingress: str
    cpu_capacity: float = 100.0
    cpu_used: float = 0.0

    @property
    def cpu_available(self):
        return self.cpu_capacity - self.cpu_used


def add_link(graph, source, target, capacity, kind):
    graph.add_edge(
        source,
        target,
        kind=kind,
        bandwidth_capacity=capacity,
        bandwidth_used=0.0,
    )


def build_topology():
    graph = nx.Graph()
    edges = {}
    servers = {}
    cameras = {}

    k = FAT_TREE_K
    if k < 2 or k % 2 != 0:
        raise ValueError("FAT_TREE_K phải là số chẵn, >= 2")

    if CLOUD_SERVERS != k ** 3 // 4:
        raise ValueError("Số server không khớp fat-tree")

    if len(EDGE_INGRESS) != EDGE_CLUSTERS:
        raise ValueError("Số điểm ingress không khớp số cụm edge")

    # 1. Backbone Atlanta
    for index in range(1, 16):
        graph.add_node(f"N{index}", kind="backbone")

    for source, target in ATLANTA_LINKS:
        add_link(
            graph, source, target,
            CORE_LINK_MBPS, "backbone",
        )

    # 2. Các cụm edge hình sao
    for cluster in range(EDGE_CLUSTERS):
        ingress = EDGE_INGRESS[cluster]
        gateway = f"edge_gateway_{cluster}"

        graph.add_node(gateway, kind="edge_gateway")

        # Uplink tạm: cần đối chiếu cấu hình gốc
        add_link(
            graph, gateway, ingress,
            CORE_LINK_MBPS, "edge_uplink",
        )

        for index in range(DEVICES_PER_CLUSTER):
            edge_id = f"edge_{cluster}_{index}"

            device = Device(
                device_id=edge_id,
                tier="edge",
                ingress=ingress,
            )

            edges[edge_id] = device
            graph.add_node(
                edge_id, kind="edge_device", device=device,
            )

            add_link(
                graph, edge_id, gateway,
                EDGE_LINK_MBPS, "edge_access",
            )

            for camera in range(CAMERAS_PER_DEVICE):
                camera_id = f"{edge_id}_camera_{camera}"
                cameras[camera_id] = edge_id

    # 3. Fat-tree DC
    half = k // 2

    for group in range(half):
        for index in range(half):
            graph.add_node(
                f"dc_core_{group}_{index}",
                kind="dc_core_switch",
            )

    server_index = 0

    for pod in range(k):
        for agg in range(half):
            agg_id = f"dc_agg_{pod}_{agg}"
            graph.add_node(
                agg_id, kind="dc_aggregation_switch",
            )

            for index in range(half):
                add_link(
                    graph,
                    agg_id,
                    f"dc_core_{agg}_{index}",
                    DC_LINK_MBPS,
                    "dc_aggregation_core",
                )

        for edge in range(half):
            edge_switch = f"dc_edge_{pod}_{edge}"
            graph.add_node(
                edge_switch, kind="dc_edge_switch",
            )

            for agg in range(half):
                add_link(
                    graph,
                    edge_switch,
                    f"dc_agg_{pod}_{agg}",
                    DC_LINK_MBPS,
                    "dc_edge_aggregation",
                )

            for _ in range(half):
                server_id = f"server_{server_index}"

                device = Device(
                    device_id=server_id,
                    tier="cloud",
                    ingress=CLOUD_INGRESS,
                )

                servers[server_id] = device
                graph.add_node(
                    server_id,
                    kind="cloud_server",
                    device=device,
                )

                add_link(
                    graph, edge_switch, server_id,
                    DC_LINK_MBPS, "dc_server_access",
                )

                server_index += 1

    # 4. Nối DC với backbone
    # Cấu hình gateway tạm: cần đối chiếu [11]
    dc_gateway = "dc_gateway"
    graph.add_node(dc_gateway, kind="dc_gateway")

    add_link(
        graph, CLOUD_INGRESS, dc_gateway,
        CORE_LINK_MBPS, "dc_uplink",
    )

    for group in range(half):
        for index in range(half):
            add_link(
                graph,
                dc_gateway,
                f"dc_core_{group}_{index}",
                DC_LINK_MBPS,
                "dc_gateway_core",
            )

    return graph, edges, servers, cameras


if __name__ == "__main__":
    graph, edges, servers, cameras = build_topology()

    print("Thiết bị edge:", len(edges))
    print("Server cloud:", len(servers))
    print("Camera:", len(cameras))
    print("Liên kết Atlanta:", len(ATLANTA_LINKS))
    print("Tổng số nút:", graph.number_of_nodes())
    print("Tổng số liên kết:", graph.number_of_edges())
    print("Mạng liên thông:", nx.is_connected(graph))

    source = "edge_0_0"
    target = "server_0"

    # Chỉ kiểm tra có đường đi; chưa phải routing theo [11]
    path = nx.shortest_path(graph, source, target)

    print("\nĐường kiểm tra edge -> cloud:")
    print(" -> ".join(path))

    available_bandwidth = min(
        graph[u][v]["bandwidth_capacity"]
        - graph[u][v]["bandwidth_used"]
        for u, v in zip(path, path[1:])
    )

    print("BW còn lại nhỏ nhất trên tuyến:", available_bandwidth, "Mbps")