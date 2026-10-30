import run_energy_experiment as experiment

from simulation import Simulation


# Parameters obtained from reference [11].
EDGE_BASE_POWER_W = 1.21
CLOUD_BASE_POWER_W = 205.10

EDGE_CNF_POWER_W = {
    "Cap": 0.43,
    "Dec": 0.09,
    "Den": 0.11,

    # No verified value has been obtained for edge LoS.
    "LoS": None,
}

CLOUD_CNF_POWER_W = {
    "Dec": 1.67,
    "Den": 7.23,
    "LoS": 5.52,
}


class PowerSimulation(Simulation):
    """
    Extend the existing simulation with operating-energy accounting.

    Energy is integrated between events, before placement changes.

    Current operating assumption:
        A device contributes base power while hosting active CNFs.

    Missing edge-LoS power and switch power are reported explicitly.
    """

    def __init__(self, graph, edges, servers, policy):
        super().__init__(graph, edges, servers, policy)
        self.power_hourly = {}

    def power_record(self, hour_index):
        if hour_index not in self.power_hourly:
            self.power_hourly[hour_index] = {
                "edge_known_energy_j": 0.0,
                "cloud_operating_energy_j": 0.0,
                "edge_los_instance_seconds": 0.0,
            }

        return self.power_hourly[hour_index]

    def current_operating_power(self):
        active_edges = set()
        active_servers = set()

        edge_cnf_power = 0.0
        cloud_cnf_power = 0.0
        missing_edge_los_count = 0

        for allocation in self.manager.active.values():
            for cnf, device_id in allocation.placement.items():

                if device_id in self.manager.edges:
                    active_edges.add(device_id)

                    power = EDGE_CNF_POWER_W[cnf]

                    if power is None:
                        missing_edge_los_count += 1
                    else:
                        edge_cnf_power += power

                elif device_id in self.manager.servers:
                    active_servers.add(device_id)
                    cloud_cnf_power += CLOUD_CNF_POWER_W[cnf]

                else:
                    raise ValueError(
                        f"Unknown computing device: {device_id}"
                    )

        edge_power = (
            len(active_edges) * EDGE_BASE_POWER_W
            + edge_cnf_power
        )

        cloud_power = (
            len(active_servers) * CLOUD_BASE_POWER_W
            + cloud_cnf_power
        )

        return (
            edge_power,
            cloud_power,
            missing_edge_los_count,
        )

    def measure_until(self, target_time):
        target_time = float(target_time)

        if target_time < self.last_measurement:
            raise ValueError("Simulation time cannot move backwards.")

        edge_w, cloud_w, missing_los = (
            self.current_operating_power()
        )

        current = self.last_measurement

        while current < target_time:
            hour_index = int(current // 3600)
            boundary = (hour_index + 1) * 3600
            interval_end = min(target_time, boundary)
            duration = interval_end - current

            record = self.power_record(hour_index)

            record["edge_known_energy_j"] += (
                edge_w * duration
            )

            record["cloud_operating_energy_j"] += (
                cloud_w * duration
            )

            record["edge_los_instance_seconds"] += (
                missing_los * duration
            )

            current = interval_end

        # Preserve the original acceptance/utilization accounting.
        super().measure_until(target_time)

    def run(self, requests):
        rows = super().run(requests)

        for row in rows:
            record = self.power_record(row["hour"] - 1)

            edge_j = record["edge_known_energy_j"]
            cloud_j = record["cloud_operating_energy_j"]

            known_computing_j = edge_j + cloud_j

            row.update({
                "edge_known_operating_energy_j": edge_j,
                "cloud_operating_energy_j": cloud_j,

                "known_computing_average_kw": (
                    known_computing_j / 3600 / 1000
                ),

                "edge_los_instance_seconds": (
                    record["edge_los_instance_seconds"]
                ),

                "edge_los_power_missing": (
                    record["edge_los_instance_seconds"] > 0
                ),

                "switch_power_missing": True,

                # Leave unavailable quantities empty in the CSV.
                "total_system_average_kw": None,
            })

        return rows


def main():
    # Reuse the existing workload, policies, lifecycle accounting,
    # logging and CSV export, with operating-energy measurement added.
    experiment.Simulation = PowerSimulation
    experiment.main()

    print()
    print("Operating-energy accounting has been added.")
    print("Output: experiment_hourly_energy_raw.csv")
    print(
        "Total system kW remains unavailable until "
        "missing power parameters and lifecycle accounting "
        "are resolved."
    )


if __name__ == "__main__":
    main()