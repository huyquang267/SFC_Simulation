import copy
import networkx as nx

from config import CNF_ORDER, CPU_EDGE, CPU_CLOUD
from embedding import ResourceManager


EPSILON = 1e-9


def device_number(device_id):
    return tuple(
        int(part)
        for part in device_id.split("_")[1:]
    )


class CloudPolicy:
    name = "Cloud"

    @staticmethod
    def empty_copy(manager):
        graph, edges, servers = copy.deepcopy(
            (
                manager.graph,
                manager.edges,
                manager.servers,
            )
        )

        for device in list(edges.values()) + list(
            servers.values()
        ):
            device.cpu_used = 0.0

        for _, _, data in graph.edges(data=True):
            data["bandwidth_used"] = 0.0

        return ResourceManager(
            graph,
            edges,
            servers,
        )

    def plan_edge_prefixes(self, manager, requests):
        return {
            request.request_id: 1
            for request in requests
        }, "planned"

    @staticmethod
    def place_cloud_cnfs(manager, cnfs, placement):
        planned_cpu = {}

        server_ids = sorted(
            manager.servers,
            key=device_number,
        )

        for cnf in cnfs:
            demand = CPU_CLOUD[cnf]
            chosen_server = None

            for server_id in server_ids:
                server = manager.servers[server_id]

                remaining_cpu = (
                    server.cpu_capacity
                    - server.cpu_used
                    - planned_cpu.get(server_id, 0.0)
                )

                if remaining_cpu + EPSILON >= demand:
                    chosen_server = server_id
                    break

            if chosen_server is None:
                return None

            placement[cnf] = chosen_server

            planned_cpu[chosen_server] = (
                planned_cpu.get(chosen_server, 0.0)
                + demand
            )

        return placement

    def choose_placement(
        self,
        manager,
        request,
        edge_count,
    ):
        placement = {
            cnf: request.source_device
            for cnf in CNF_ORDER[:edge_count]
        }

        return self.place_cloud_cnfs(
            manager,
            CNF_ORDER[edge_count:],
            placement,
        )

    def admit(self, manager, new_request):
        if new_request.request_id in manager.active:
            return None, "request_already_active", []

        if new_request.camera_id in (
            manager.active_cameras
        ):
            return None, "camera_already_active", []

        if new_request.source_device not in manager.edges:
            return None, "invalid_source_device", []

        requests = [
            allocation.request
            for allocation in manager.active.values()
        ]

        requests.append(new_request)

        requests.sort(
            key=lambda request: (
                request.arrival_time,
                request.request_id,
            )
        )

        candidate = self.empty_copy(manager)

        prefixes, reason = self.plan_edge_prefixes(
            candidate,
            requests,
        )

        if prefixes is None:
            return None, reason, []

        for request in requests:
            placement = self.choose_placement(
                candidate,
                request,
                prefixes[request.request_id],
            )

            if placement is None:
                return None, "insufficient_cloud_cpu", []

            allocation, reason = candidate.embed(
                request,
                placement,
            )

            if allocation is None:
                return (
                    None,
                    f"mapping_failed: {reason}",
                    [],
                )

        relocations = []

        for request_id, old_allocation in (
            manager.active.items()
        ):
            new_allocation = candidate.active[
                request_id
            ]

            for cnf, old_device in (
                old_allocation.placement.items()
            ):
                new_device = (
                    new_allocation.placement[cnf]
                )

                if old_device != new_device:
                    relocations.append(
                        (
                            request_id,
                            cnf,
                            old_device,
                            new_device,
                        )
                    )

        return candidate, "accepted", relocations


class EdgeCloudPolicy(CloudPolicy):
    name = "Edge-cloud"

    def plan_edge_prefixes(self, manager, requests):
        prefixes = {
            request.request_id: len(CNF_ORDER)
            for request in requests
        }

        requests_by_edge = {}

        for request in requests:
            requests_by_edge.setdefault(
                request.source_device,
                [],
            ).append(request)

        for edge_id, edge_requests in (
            requests_by_edge.items()
        ):
            capacity = (
                manager.edges[edge_id].cpu_capacity
            )

            def edge_cpu_demand():
                return sum(
                    CPU_EDGE[cnf]
                    for request in edge_requests
                    for cnf in CNF_ORDER[
                        :prefixes[request.request_id]
                    ]
                )

            while edge_cpu_demand() > capacity + EPSILON:
                movable = [
                    request
                    for request in edge_requests
                    if prefixes[request.request_id] > 1
                ]

                if not movable:
                    return (
                        None,
                        f"insufficient_edge_cpu: {edge_id}",
                    )

                selected = max(
                    movable,
                    key=lambda request: (
                        prefixes[request.request_id],
                        request.arrival_time,
                        request.request_id,
                    ),
                )

                prefixes[selected.request_id] -= 1

        return prefixes, "planned"


class EdgeCloudEOPolicy(EdgeCloudPolicy):
    name = "Edge-cloud + EO"

    def plan_edge_prefixes(self, manager, requests):
        prefixes, reason = super().plan_edge_prefixes(
            manager,
            requests,
        )

        if prefixes is None:
            return None, reason

        # Giữ trước CPU cho phần chạy ở edge nguồn
        # của TẤT CẢ SFC, kể cả SFC chưa được embed.
        #
        # Nhờ vậy EO không lấy mất CPU mà các SFC
        # ở chính thiết bị đó đã cần dùng.
        self.edge_reserved = {
            edge_id: 0.0
            for edge_id in manager.edges
        }

        for request in requests:
            count = prefixes[request.request_id]

            self.edge_reserved[
                request.source_device
            ] += sum(
                CPU_EDGE[cnf]
                for cnf in CNF_ORDER[:count]
            )

        return prefixes, "planned"

    @staticmethod
    def nearby_edges(manager, source_device):
        source_cluster = device_number(
            source_device
        )[0]

        source_ingress = manager.edges[
            source_device
        ].ingress

        # Chỉ dùng mạng backbone để đo khoảng cách cụm.
        backbone_nodes = [
            node
            for node in manager.graph
            if (
                isinstance(node, str)
                and node.startswith("N")
                and node[1:].isdigit()
            )
        ]

        backbone = manager.graph.subgraph(
            backbone_nodes
        )

        distances = (
            nx.single_source_shortest_path_length(
                backbone,
                source_ingress,
            )
        )

        def rank(edge_id):
            cluster = device_number(edge_id)[0]

            ingress = manager.edges[
                edge_id
            ].ingress

            return (
                0 if cluster == source_cluster else 1,
                distances.get(ingress, float("inf")),
                device_number(edge_id),
            )

        return sorted(
            (
                edge_id
                for edge_id in manager.edges
                if edge_id != source_device
            ),
            key=rank,
        )

    def choose_placement(
        self,
        manager,
        request,
        edge_count,
    ):
        placement = {
            cnf: request.source_device
            for cnf in CNF_ORDER[:edge_count]
        }

        cloud_cnfs = []

        neighbors = self.nearby_edges(
            manager,
            request.source_device,
        )

        for cnf in CNF_ORDER[edge_count:]:
            chosen_edge = None
            demand = CPU_EDGE[cnf]

            for edge_id in neighbors:
                remaining_cpu = (
                    manager.edges[edge_id].cpu_capacity
                    - self.edge_reserved[edge_id]
                )

                if remaining_cpu + EPSILON >= demand:
                    chosen_edge = edge_id
                    break

            if chosen_edge is None:
                cloud_cnfs.append(cnf)
            else:
                placement[cnf] = chosen_edge

                self.edge_reserved[
                    chosen_edge
                ] += demand

        return self.place_cloud_cnfs(
            manager,
            cloud_cnfs,
            placement,
        )