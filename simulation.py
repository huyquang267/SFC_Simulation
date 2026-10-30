import csv
import math
from collections import Counter

import simpy

from config import MAX_SFC
from embedding import ResourceManager


class Simulation:
    def __init__(self, graph, edges, servers, policy):
        self.env = simpy.Environment()
        self.manager = ResourceManager(graph, edges, servers)
        self.policy = policy

        self.last_measurement = 0.0
        self.hourly = {}
        self.lifecycle_events = []
        self.event_id = 0

    def log(self, message):
        print(f"[{self.env.now:10.2f}s] {message}")

    def hour_record(self, hour_index):
        if hour_index not in self.hourly:
            self.hourly[hour_index] = {
                "arrived": 0,
                "accepted": 0,
                "rejected": 0,
                "completed": 0,
                "existing_cnf_relocations": 0,
                "cloud_cnf_created": 0,
                "cloud_cnf_terminated": 0,
                "active_sfc_seconds": 0.0,
            }

        return self.hourly[hour_index]

    def measure_until(self, target_time):
        """Integrate active SFC count over simulation time."""
        target_time = float(target_time)

        if target_time < self.last_measurement:
            raise ValueError("Simulation time cannot move backwards.")

        active_count = len(self.manager.active)
        current = self.last_measurement

        while current < target_time:
            hour_index = int(current // 3600)
            boundary = (hour_index + 1) * 3600
            interval_end = min(target_time, boundary)

            record = self.hour_record(hour_index)
            record["active_sfc_seconds"] += (
                active_count * (interval_end - current)
            )

            current = interval_end

        self.last_measurement = target_time

    def cloud_instances(self, manager):
        """Identify each cloud CNF by request ID and CNF name."""
        instances = {}

        for request_id, allocation in manager.active.items():
            for cnf, device_id in allocation.placement.items():
                if device_id in manager.servers:
                    instances[(request_id, cnf)] = device_id

        return instances

    def record_lifecycle(self, before, after, trigger):
        """Group creation/deletion by server at one decision event."""
        created = Counter()
        terminated = Counter()

        for instance, old_server in before.items():
            if after.get(instance) != old_server:
                terminated[old_server] += 1

        for instance, new_server in after.items():
            if before.get(instance) != new_server:
                created[new_server] += 1

        if not created and not terminated:
            return

        self.event_id += 1
        hour_index = int(self.env.now // 3600)
        record = self.hour_record(hour_index)

        for action, batches in (
            ("terminate", terminated),
            ("cold_start", created),
        ):
            for server_id, count in sorted(batches.items()):
                self.lifecycle_events.append({
                    "strategy": self.policy.name,
                    "event_id": self.event_id,
                    "time_s": self.env.now,
                    "hour": hour_index + 1,
                    "trigger": trigger,
                    "server_id": server_id,
                    "action": action,
                    "cnf_count": count,
                    "within_profile_range": 10 <= count <= 100,
                })

                if action == "cold_start":
                    record["cloud_cnf_created"] += count
                else:
                    record["cloud_cnf_terminated"] += count

    def handle_request(self, request):
        yield self.env.timeout(
            request.arrival_time - self.env.now
        )

        self.measure_until(self.env.now)

        hour_index = int(self.env.now // 3600)
        record = self.hour_record(hour_index)
        record["arrived"] += 1

        before = self.cloud_instances(self.manager)

        candidate, reason, relocations = self.policy.admit(
            self.manager, request
        )

        if candidate is None:
            record["rejected"] += 1
            self.log(
                f"SFC {request.request_id} REJECTED | {reason}"
            )
            return

        self.manager = candidate

        record["accepted"] += 1
        record["existing_cnf_relocations"] += len(relocations)

        after = self.cloud_instances(self.manager)

        self.record_lifecycle(
            before,
            after,
            trigger=f"arrival:{request.request_id}",
        )

        self.log(
            f"SFC {request.request_id} ACCEPTED | "
            f"existing CNF relocations={len(relocations)}"
        )

        # The current model keeps the original service duration.
        # Lifecycle latency is not yet inserted into this timeout.
        yield self.env.timeout(request.service_time)

        self.measure_until(self.env.now)

        before = self.cloud_instances(self.manager)
        self.manager.release(request.request_id)
        after = self.cloud_instances(self.manager)

        self.record_lifecycle(
            before,
            after,
            trigger=f"departure:{request.request_id}",
        )

        hour_index = int(self.env.now // 3600)
        self.hour_record(hour_index)["completed"] += 1

        self.log(f"SFC {request.request_id} COMPLETED")

    def save_lifecycle_events(self):
        strategy_key = {
            "Cloud": "cloud",
            "Edge-cloud": "edge_cloud",
            "Edge-cloud + EO": "edge_cloud_eo",
        }.get(self.policy.name)

        if strategy_key is None:
            raise ValueError(
                f"Unknown strategy: {self.policy.name}"
            )

        filename = f"lifecycle_events_{strategy_key}.csv"

        fields = [
            "strategy",
            "event_id",
            "time_s",
            "hour",
            "trigger",
            "server_id",
            "action",
            "cnf_count",
            "within_profile_range",
        ]

        with open(
            filename, "w", newline="", encoding="utf-8-sig"
        ) as file:
            writer = csv.DictWriter(file, fieldnames=fields)
            writer.writeheader()
            writer.writerows(self.lifecycle_events)

        self.log(f"Saved: {filename}")

    def run(self, requests):
        requests = list(requests)

        for request in requests:
            self.env.process(self.handle_request(request))

        self.env.run()

        nominal_end = max(
            (
                request.arrival_time + request.service_time
                for request in requests
            ),
            default=0.0,
        )

        end_time = max(self.env.now, nominal_end)
        hours = max(1, math.ceil(end_time / 3600))

        self.measure_until(hours * 3600)

        rows = []

        for hour_index in range(hours):
            record = self.hour_record(hour_index)

            arrived = record["arrived"]
            accepted = record["accepted"]

            average_active = (
                record["active_sfc_seconds"] / 3600
            )

            rows.append({
                "strategy": self.policy.name,
                "hour": hour_index + 1,
                "arrived": arrived,
                "accepted": accepted,
                "rejected": record["rejected"],
                "completed": record["completed"],
                "existing_cnf_relocations": (
                    record["existing_cnf_relocations"]
                ),
                "acceptance_ratio": (
                    accepted / arrived if arrived else None
                ),
                "average_active_sfcs": average_active,
                "average_utilization": (
                    average_active / MAX_SFC
                ),
                "cloud_cnf_created": (
                    record["cloud_cnf_created"]
                ),
                "cloud_cnf_terminated": (
                    record["cloud_cnf_terminated"]
                ),
            })

        self.save_lifecycle_events()
        return rows