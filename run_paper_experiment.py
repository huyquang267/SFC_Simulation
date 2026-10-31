"""
Chạy thực nghiệm tái hiện 4 hình kết quả của bài báo:

- Fig. 5: Acceptance ratio theo Offered load.
- Fig. 6: System power (kW) theo Offered load, Real và Ideal.
- Fig. 7: System power (kW) theo Utilization, Real và Ideal.
- Fig. 8: Downtime (s) theo Offered load.

Mô hình chi phí theo GVHD (xem cost_model.py):
- power = min(power_max, power_base + cpu_usage * power_weight),
  Cloud server rảnh -> 0 W.
  Ideal: chỉ CPU CNF active. Real: + CPU spike khi Cold Start.
- Downtime hệ thống = tổng (thời gian reconfig từng SFC),
  thời gian reconfig 1 SFC = max(t_null->warm) các CNF của nó.

Cách dùng:
    python run_paper_experiment.py                # đủ 10 mức tải
    python run_paper_experiment.py --hours 2      # chạy thử nhanh
    python run_paper_experiment.py --seeds 0 1 2  # trung bình nhiều seed
"""

import argparse
import csv
import time
from contextlib import redirect_stdout

from config import ARRIVALS_PER_HOUR, OFFERED_LOADS
from topology import build_topology
from workload import generate_requests
from policies import (
    CloudPolicy,
    EdgeCloudPolicy,
    EdgeCloudEOPolicy,
)
from simulation import Simulation
from lifecycle_profile import cloud_cold_start
from cost_model import (
    SCENARIO_IDEAL,
    SCENARIO_REAL,
    computing_power,
    system_reconfig_downtime,
)


# False: chỉ SFC đang chạy bị đổi placement mới tính là reconfig.
# True : tính cả lần deploy đầu tiên của SFC mới đến.
INCLUDE_NEW_SFCS_IN_DOWNTIME = False

OUTPUT_CSV = "paper_results.csv"

STRATEGIES = [
    ("cloud", CloudPolicy),
    ("edge_cloud", EdgeCloudPolicy),
    ("edge_cloud_eo", EdgeCloudEOPolicy),
]


class PaperSimulation(Simulation):
    """Simulation + tích phân công suất (Ideal/Real) + downtime."""

    def __init__(self, graph, edges, servers, policy):
        super().__init__(graph, edges, servers, policy)
        # Các cửa sổ cold start đang diễn ra:
        # (thời điểm kết thúc, server_id, %CPU spike).
        self.spikes = []
        self.cost_hourly = {}

    def cost_record(self, hour_index):
        if hour_index not in self.cost_hourly:
            self.cost_hourly[hour_index] = {
                "ideal_energy_j": 0.0,
                "real_energy_j": 0.0,
                "active_server_seconds": 0.0,
                "downtime_s": 0.0,
                "reconfigured_sfcs": 0,
                "reconfig_events": 0,
            }
        return self.cost_hourly[hour_index]

    # ---------------- Downtime + cold start ----------------

    def after_admission(self, old_manager):
        total, affected, batches = system_reconfig_downtime(
            old_manager.active,
            self.manager.active,
            self.manager.servers,
            include_new_sfcs=INCLUDE_NEW_SFCS_IN_DOWNTIME,
        )

        record = self.cost_record(int(self.env.now // 3600))
        record["downtime_s"] += total
        record["reconfigured_sfcs"] += affected
        if affected:
            record["reconfig_events"] += 1

        # Real: mỗi batch cold start tạo spike CPU trên server
        # trong suốt thời gian NULL -> WARM của batch.
        for server_id, count in batches.items():
            cost = cloud_cold_start(count, allow_extrapolation=True)
            self.spikes.append((
                self.env.now + cost.duration_s,
                server_id,
                cost.cpu_percent,
            ))

    # ---------------- Tích phân công suất ----------------

    def spike_at(self, t):
        spike = {}
        for end, server_id, cpu in self.spikes:
            if end > t:
                spike[server_id] = spike.get(server_id, 0.0) + cpu
        return spike

    def measure_until(self, target_time):
        target_time = float(target_time)
        current = self.last_measurement

        if target_time > current:
            ideal = computing_power(self.manager, SCENARIO_IDEAL)
            ideal_w = ideal["computing_power_w"]

            while current < target_time:
                hour_index = int(current // 3600)
                boundary = (hour_index + 1) * 3600
                next_expiry = min(
                    (end for end, _, _ in self.spikes if end > current),
                    default=float("inf"),
                )
                interval_end = min(target_time, boundary, next_expiry)
                duration = interval_end - current

                spike = self.spike_at(current)
                if spike:
                    real_w = computing_power(
                        self.manager, SCENARIO_REAL, cpu_spike=spike
                    )["computing_power_w"]
                else:
                    real_w = ideal_w

                record = self.cost_record(hour_index)
                record["ideal_energy_j"] += ideal_w * duration
                record["real_energy_j"] += real_w * duration
                record["active_server_seconds"] += (
                    ideal["active_servers"] * duration
                )

                current = interval_end

            # Bỏ các spike đã hết hạn.
            self.spikes = [s for s in self.spikes if s[0] > target_time]

        super().measure_until(target_time)

    def run(self, requests):
        rows = super().run(requests)

        for row in rows:
            record = self.cost_record(row["hour"] - 1)
            reconf = record["reconfigured_sfcs"]
            row.update({
                "ideal_power_kw": record["ideal_energy_j"] / 3600 / 1000,
                "real_power_kw": record["real_energy_j"] / 3600 / 1000,
                "avg_active_servers": (
                    record["active_server_seconds"] / 3600
                ),
                "downtime_s": record["downtime_s"],
                "reconfigured_sfcs": reconf,
                "reconfig_events": record["reconfig_events"],
                "downtime_per_reconfig_s": (
                    record["downtime_s"] / reconf if reconf else 0.0
                ),
            })

        return rows


def save_csv(filename, rows):
    with open(filename, "w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"Saved: {filename}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=int, default=len(ARRIVALS_PER_HOUR),
                        help="Số mức tải (giờ) cần chạy, tối đa 10.")
    parser.add_argument("--seeds", type=int, nargs="+", default=[0])
    parser.add_argument("--strategies", nargs="+",
                        default=[key for key, _ in STRATEGIES])
    parser.add_argument("--output", default=OUTPUT_CSV)
    args = parser.parse_args()

    arrivals = ARRIVALS_PER_HOUR[:args.hours]
    loads = OFFERED_LOADS[:args.hours]
    all_rows = []

    for seed in args.seeds:
        _, _, _, cameras = build_topology()
        requests = generate_requests(cameras, arrivals, seed=seed)
        print(f"Seed {seed}: {len(requests)} requests, "
              f"arrivals/hour = {arrivals}")

        for key, policy_class in STRATEGIES:
            if key not in args.strategies:
                continue

            started = time.time()
            print(f"  Running {key} ...", flush=True)

            graph, edges, servers, _ = build_topology()
            simulation = PaperSimulation(
                graph, edges, servers, policy_class()
            )

            with open(f"paper_{key}_seed{seed}.log", "w",
                      encoding="utf-8") as log_file:
                with redirect_stdout(log_file):
                    rows = simulation.run(requests)

            for row in rows:
                index = row["hour"] - 1
                in_arrivals = index < len(arrivals)
                row.update({
                    "seed": seed,
                    "phase": "arrivals" if in_arrivals else "drain",
                    "offered_load_pct": (
                        100 * loads[index] if in_arrivals else 0
                    ),
                    "configured_arrivals_per_hour": (
                        arrivals[index] if in_arrivals else 0
                    ),
                })
                all_rows.append(row)

                if in_arrivals:
                    print(
                        f"    load {row['offered_load_pct']:5.1f}% | "
                        f"acc={row['acceptance_ratio']:.3f} | "
                        f"util={100 * row['average_utilization']:5.1f}% | "
                        f"P_ideal={row['ideal_power_kw']:6.2f} kW | "
                        f"P_real={row['real_power_kw']:6.2f} kW | "
                        f"downtime={row['downtime_s']:8.1f} s "
                        f"({row['reconfigured_sfcs']} SFC)",
                        flush=True,
                    )

            print(f"  Done {key} in {time.time() - started:.1f} s")

    save_csv(args.output, all_rows)


if __name__ == "__main__":
    main()
