import csv
from contextlib import redirect_stdout

from topology import build_topology
from workload import generate_requests
from simulation import Simulation

from policies import (
    CloudPolicy,
    EdgeCloudPolicy,
    EdgeCloudEOPolicy,
)


# Hai giờ đầu để kiểm tra chạy workload.
ARRIVALS_PER_HOUR = [210, 420]

# Cố định seed để chạy lại cùng dữ liệu.
SEED = 0


class ExperimentSimulation(Simulation):
    def __init__(self, graph, edges, servers, policy):
        super().__init__(
            graph, edges, servers, policy
        )
        self.camera_busy_by_hour = {}

    def log(self, message):
        if "camera_already_active" in message:
            hour = int(self.env.now // 3600.0)

            self.camera_busy_by_hour[hour] = (
                self.camera_busy_by_hour.get(hour, 0)
                + 1
            )

        super().log(message)


def save_csv(path, rows):
    with open(
        path,
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=list(rows[0]),
        )
        writer.writeheader()
        writer.writerows(rows)


def main():
    _, _, _, cameras = build_topology()

    # Sinh một bộ yêu cầu chung cho cả ba phương án.
    requests = generate_requests(
        camera_sources=cameras,
        arrivals_per_hour=ARRIVALS_PER_HOUR,
        seed=SEED,
    )

    print(f"Seed: {SEED}")
    print(f"Generated requests: {len(requests)}")

    if not requests:
        raise RuntimeError("Workload không có yêu cầu.")

    request_rows = [
        {
            "request_id": request.request_id,
            "camera_id": request.camera_id,
            "source_device": request.source_device,
            "arrival_time_s": request.arrival_time,
            "service_time_s": request.service_time,
        }
        for request in requests
    ]

    save_csv("experiment_requests.csv", request_rows)

    all_rows = []

    experiments = [
        ("cloud", CloudPolicy()),
        ("edge_cloud", EdgeCloudPolicy()),
        ("edge_cloud_eo", EdgeCloudEOPolicy()),
    ]

    for file_label, policy in experiments:
        print(f"\nRunning: {policy.name}", flush=True)

        graph, edges, servers, _ = build_topology()

        simulation = ExperimentSimulation(
            graph,
            edges,
            servers,
            policy,
        )

        log_path = f"experiment_{file_label}.log"

        # Log chi tiết ra file, tránh tràn Terminal.
        with open(
            log_path,
            "w",
            encoding="utf-8",
        ) as log_file:
            with redirect_stdout(log_file):
                rows = simulation.run(requests)

        for row in rows:
            hour_index = row["hour"] - 1

            if hour_index < len(ARRIVALS_PER_HOUR):
                rate = ARRIVALS_PER_HOUR[hour_index]
                phase = "arrivals"
            else:
                rate = 0
                phase = "drain"

            row["seed"] = SEED
            row["phase"] = phase
            row["configured_arrivals_per_hour"] = rate

            row["camera_busy_rejections"] = (
                simulation.camera_busy_by_hour.get(
                    hour_index, 0
                )
            )

            all_rows.append(row)

            if phase == "arrivals":
                acceptance = row["acceptance_ratio"]

                acceptance_text = (
                    f"{acceptance:.2%}"
                    if acceptance is not None
                    else "N/A"
                )

                print(
                    f"Hour {row['hour']}: "
                    f"arrived={row['arrived']}, "
                    f"accepted={row['accepted']}, "
                    f"rejected={row['rejected']}, "
                    f"camera_busy="
                    f"{row['camera_busy_rejections']}, "
                    f"acceptance={acceptance_text}, "
                    f"utilization="
                    f"{row['average_utilization']:.2%}"
                )

        print(f"Detailed log: {log_path}")

    save_csv(
        "experiment_hourly_results.csv",
        all_rows,
    )

    print("\nSaved: experiment_requests.csv")
    print("Saved: experiment_hourly_results.csv")


if __name__ == "__main__":
    main()