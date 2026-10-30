import csv
from contextlib import redirect_stdout

from topology import build_topology
from workload import generate_requests
from policies import (
    CloudPolicy,
    EdgeCloudPolicy,
    EdgeCloudEOPolicy,
)
from simulation import Simulation
from lifecycle_profile import (
    cloud_cold_start,
    cloud_termination,
)


ARRIVALS_PER_HOUR = [210, 420]
SEED = 0


def save_csv(filename, rows):
    if not rows:
        return

    with open(
        filename, "w", newline="", encoding="utf-8-sig"
    ) as file:
        writer = csv.DictWriter(
            file, fieldnames=list(rows[0].keys())
        )
        writer.writeheader()
        writer.writerows(rows)

    print(f"Saved: {filename}")


def calculate_lifecycle_energy(events):
    detailed_rows = []
    hourly = {}

    for event in events:
        count = event["cnf_count"]
        action = event["action"]
        hour = event["hour"]

        # Explicit extrapolation for batches outside [10, 100].
        # These estimates are exported as RAW values.
        if action == "cold_start":
            cost = cloud_cold_start(
                count, allow_extrapolation=True
            )
        elif action == "terminate":
            cost = cloud_termination(
                count, allow_extrapolation=True
            )
        else:
            raise ValueError(f"Unknown action: {action}")

        if hour not in hourly:
            hourly[hour] = {
                "cold_energy_j": 0.0,
                "terminate_energy_j": 0.0,
                "extrapolated_batches": 0,
                "batch_count": 0,
            }

        summary = hourly[hour]
        summary["batch_count"] += 1

        if not event["within_profile_range"]:
            summary["extrapolated_batches"] += 1

        if action == "cold_start":
            summary["cold_energy_j"] += cost.energy_j
        else:
            summary["terminate_energy_j"] += cost.energy_j

        detailed_rows.append({
            **event,
            "profile_cpu_percent": cost.cpu_percent,
            "profile_duration_s": cost.duration_s,
            "profile_power_w_raw": cost.power_w,
            "profile_energy_j_raw": cost.energy_j,
            "extrapolated": not event["within_profile_range"],
            "termination_coefficient_unverified": (
                action == "terminate"
            ),
        })

    return detailed_rows, hourly


def main():
    _, _, _, cameras = build_topology()

    requests = generate_requests(
        cameras,
        ARRIVALS_PER_HOUR,
        seed=SEED,
    )

    print(f"Common workload: {len(requests)} requests")

    all_hourly_rows = []
    all_event_rows = []

    strategies = [
        ("cloud", CloudPolicy),
        ("edge_cloud", EdgeCloudPolicy),
        ("edge_cloud_eo", EdgeCloudEOPolicy),
    ]

    for strategy_key, policy_class in strategies:
        print(f"Running: {strategy_key}", flush=True)

        graph, edges, servers, _ = build_topology()

        simulation = Simulation(
            graph,
            edges,
            servers,
            policy_class(),
        )

        log_filename = (
            f"experiment_energy_{strategy_key}.log"
        )

        with open(
            log_filename, "w", encoding="utf-8"
        ) as log_file:
            with redirect_stdout(log_file):
                rows = simulation.run(requests)

        detailed, energy_by_hour = (
            calculate_lifecycle_energy(
                simulation.lifecycle_events
            )
        )

        all_event_rows.extend(detailed)

        for row in rows:
            hour = row["hour"]
            hour_index = hour - 1

            energy = energy_by_hour.get(hour, {
                "cold_energy_j": 0.0,
                "terminate_energy_j": 0.0,
                "extrapolated_batches": 0,
                "batch_count": 0,
            })

            cold_j = energy["cold_energy_j"]
            terminate_j = energy["terminate_energy_j"]
            total_j = cold_j + terminate_j

            row.update({
                "seed": SEED,
                "phase": (
                    "arrivals"
                    if hour_index < len(ARRIVALS_PER_HOUR)
                    else "drain"
                ),
                "configured_arrivals_per_hour": (
                    ARRIVALS_PER_HOUR[hour_index]
                    if hour_index < len(ARRIVALS_PER_HOUR)
                    else 0
                ),
                "cloud_cold_energy_j_raw": cold_j,
                "cloud_terminate_energy_j_raw": terminate_j,
                "cloud_lifecycle_energy_j_raw": total_j,
                "cloud_lifecycle_average_kw_raw": (
                    total_j / 3600 / 1000
                ),
                "lifecycle_batch_count": (
                    energy["batch_count"]
                ),
                "extrapolated_batch_count": (
                    energy["extrapolated_batches"]
                ),
            })

            all_hourly_rows.append(row)

            if row["phase"] == "arrivals":
                print(
                    f"  Hour {hour}: "
                    f"accepted={row['accepted']}/"
                    f"{row['arrived']}, "
                    f"created={row['cloud_cnf_created']}, "
                    f"terminated="
                    f"{row['cloud_cnf_terminated']}, "
                    f"lifecycle RAW="
                    f"{row['cloud_lifecycle_average_kw_raw']:.6f} kW"
                )

    save_csv(
        "experiment_lifecycle_energy.csv",
        all_event_rows,
    )

    save_csv(
        "experiment_hourly_energy_raw.csv",
        all_hourly_rows,
    )


if __name__ == "__main__":
    main()