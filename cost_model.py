"""
Mô hình chi phí: Công suất (Power) và Thời gian gián đoạn (Downtime).

Theo hướng dẫn GVHD:
- power = min(power_max, power_base + cpu_usage * power_weight)
  + Cloud server có cpu_usage = 0 -> power = 0 (tắt để gom cụm).
  + Ideal: cpu_usage chỉ gồm CNF đang active.
  + Real : cpu_usage cộng thêm CPU spike khi Cold Start (NULL -> WARM).
- Downtime 1 SFC = max(t_null->warm) của các CNF phải khởi động lại.
  + Edge đã giữ sẵn CNF ở ready/warm state -> 0 s.
  + Chỉ CNF được đặt (mới) lên Cloud mới tốn thời gian NULL -> WARM.
- Downtime hệ thống = tổng downtime của các SFC bị reconfig.
"""

from collections import Counter

from config import CNF_ORDER
from topology import build_topology
from workload import SFCRequest
from embedding import ResourceManager
from lifecycle_profile import cloud_cold_start


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

CPU_EDGE_REF = {"Cap": 3.0, "Dec": 8.0, "Den": 13.6}
CPU_CLOUD_REF = {"Dec": 1.5, "Den": 6.5, "LoS": 5.0}


# ---------------------------------------------------------------
# Tham số mô hình tuyến tính (suy ra từ Table III, CẦN XÁC NHẬN).
# ---------------------------------------------------------------

# Cloud: Dec 1.67/1.5, Den 7.23/6.5, LoS 5.52/5.0 đều ≈ 1.11 W/%CPU
# -> dữ liệu Table III nhất quán với mô hình tuyến tính.
CLOUD_POWER_WEIGHT_W_PER_PCT = (
    sum(CLOUD_CNF_POWER_W[c] for c in CPU_CLOUD_REF)
    / sum(CPU_CLOUD_REF.values())
)

# Edge: chỉ dùng Dec, Den (Cap đã gộp camera nên không dùng để fit).
EDGE_POWER_WEIGHT_W_PER_PCT = (
    (EDGE_CNF_POWER_W["Dec"] + EDGE_CNF_POWER_W["Den"])
    / (CPU_EDGE_REF["Dec"] + CPU_EDGE_REF["Den"])
)

# Phần công suất camera nằm trong 0.43 W của Cap.
EDGE_CAMERA_POWER_W = (
    EDGE_CNF_POWER_W["Cap"]
    - CPU_EDGE_REF["Cap"] * EDGE_POWER_WEIGHT_W_PER_PCT
)

# TODO(GVHD): chưa có power_max đo thực tế.
# Tạm lấy công suất tại 100% CPU; trần chỉ có tác dụng
# khi kịch bản Real đẩy cpu_usage vượt 100%.
CLOUD_POWER_MAX_W = CLOUD_BASE_POWER_W + 100.0 * CLOUD_POWER_WEIGHT_W_PER_PCT
EDGE_POWER_MAX_W = EDGE_BASE_POWER_W + 100.0 * EDGE_POWER_WEIGHT_W_PER_PCT

# Thầy chỉ yêu cầu tắt Cloud server rảnh. Edge là thiết bị
# hiện trường gắn camera nên mặc định luôn bật (tính base power).
EDGE_IDLE_POWER_OFF = False


# Thời gian NULL -> WARM theo CNF và vị trí (giây).
# Edge = 0 theo giả định bài báo (CNF đã sẵn ở ready state).
# Cloud = None -> dùng profile batch Table II: 4.24 + 0.70 * x,
# với x = số CNF cold start cùng lúc trên cùng server.
# TODO(GVHD): thay None bằng số đo riêng từng CNF nếu có.
NULL_TO_WARM_S = {
    "edge": {cnf: 0.0 for cnf in CNF_ORDER},
    "cloud": {"Cap": None, "Dec": None, "Den": None, "LoS": None},
}

SCENARIO_IDEAL = "ideal"
SCENARIO_REAL = "real"


# ---------------------------------------------------------------
# 1. POWER MODEL
# ---------------------------------------------------------------

def device_power(cpu_usage, power_base, power_weight, power_max,
                 off_when_idle):
    """
    power = min(power_max, power_base + cpu_usage * power_weight)
    Nếu off_when_idle và cpu_usage = 0 -> power = 0.
    """
    if cpu_usage < 0:
        raise ValueError("cpu_usage phải không âm.")

    if off_when_idle and cpu_usage <= 0:
        return 0.0

    return min(power_max, power_base + cpu_usage * power_weight)


def cold_start_spike(cold_start_batches, allow_extrapolation=True):
    """
    Đổi {server_id: số CNF đang cold start} -> {server_id: %CPU spike}
    theo Table II, Equation (1).
    """
    return {
        server_id: cloud_cold_start(
            count, allow_extrapolation=allow_extrapolation
        ).cpu_percent
        for server_id, count in cold_start_batches.items()
        if count > 0
    }


def computing_power(manager, scenario=SCENARIO_IDEAL,
                    cold_start_batches=None, cpu_spike=None):
    """
    Tính công suất tức thời của các máy tính.

    scenario:
    - "ideal": cpu_usage = CPU của CNF đang active.
    - "real" : cộng thêm CPU spike trên các server đang cold start.
      cold_start_batches = {server_id: số CNF đang NULL -> WARM}, hoặc
      cpu_spike = {server_id: %CPU spike} (simulator tự cộng dồn các
      cửa sổ cold start chồng nhau).
      Edge không có cold start (CNF đã ready) -> không có spike.

    Chưa bao gồm:
    - Switch và port mạng.
    """
    if scenario not in (SCENARIO_IDEAL, SCENARIO_REAL):
        raise ValueError(f"Kịch bản không hợp lệ: {scenario}")

    spike = {}
    if scenario == SCENARIO_REAL:
        if cpu_spike:
            spike = dict(cpu_spike)
        elif cold_start_batches:
            spike = cold_start_spike(cold_start_batches)

    active_cameras_per_edge = Counter(
        allocation.placement["Cap"]
        for allocation in manager.active.values()
    )

    edge_power = 0.0
    active_edges = 0

    for device_id, device in manager.edges.items():
        cpu = device.cpu_used
        power = device_power(
            cpu,
            EDGE_BASE_POWER_W,
            EDGE_POWER_WEIGHT_W_PER_PCT,
            EDGE_POWER_MAX_W,
            off_when_idle=EDGE_IDLE_POWER_OFF,
        )
        # Camera không thuộc CPU của edge -> cộng riêng.
        power += (
            active_cameras_per_edge[device_id] * EDGE_CAMERA_POWER_W
        )
        edge_power += power
        if cpu > 0:
            active_edges += 1

    cloud_power = 0.0
    active_servers = 0
    peak_cloud_cpu = 0.0

    for server_id, server in manager.servers.items():
        cpu = server.cpu_used + spike.get(server_id, 0.0)
        power = device_power(
            cpu,
            CLOUD_BASE_POWER_W,
            CLOUD_POWER_WEIGHT_W_PER_PCT,
            CLOUD_POWER_MAX_W,
            off_when_idle=True,  # Consolidation.
        )
        cloud_power += power
        if cpu > 0:
            active_servers += 1
        peak_cloud_cpu = max(peak_cloud_cpu, cpu)

    return {
        "scenario": scenario,
        "active_edges": active_edges,
        "active_servers": active_servers,
        "peak_cloud_cpu_pct": peak_cloud_cpu,
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


# ---------------------------------------------------------------
# 2. DOWNTIME MODEL
# ---------------------------------------------------------------

def null_to_warm_time(cnf, location, batch_size=1):
    """
    Thời gian NULL -> WARM của 1 CNF tại vị trí location.
    location: "edge" hoặc "cloud".
    """
    t = NULL_TO_WARM_S[location][cnf]

    if t is not None:
        return t

    # Cloud dùng profile batch: mọi CNF trong batch cùng xong
    # sau duration của batch.
    return cloud_cold_start(
        batch_size, allow_extrapolation=True
    ).duration_s


def cold_started_cnfs(old_placement, new_placement, servers):
    """
    CNF phải chuyển NULL -> WARM khi đổi placement.
    Chỉ tính CNF có vị trí mới là Cloud server và khác vị trí cũ.
    Di chuyển đến/giữa Edge = 0 chi phí (Edge đã ready).
    """
    old_placement = old_placement or {}
    return [
        (cnf, device_id)
        for cnf, device_id in new_placement.items()
        if device_id in servers
        and old_placement.get(cnf) != device_id
    ]


def sfc_reconfig_downtime(old_placement, new_placement, servers,
                          batch_sizes=None):
    """
    Downtime_1SFC = max(t_null->warm) của các CNF cold start.
    Dùng max (CNF khởi động song song), KHÔNG cộng tổng.

    batch_sizes: {server_id: số CNF cold start cùng lúc tại server}.
    """
    batch_sizes = batch_sizes or {}
    times = [
        null_to_warm_time(
            cnf, "cloud", batch_sizes.get(server_id, 1)
        )
        for cnf, server_id in cold_started_cnfs(
            old_placement, new_placement, servers
        )
    ]
    return max(times, default=0.0)


def system_reconfig_downtime(before_active, after_active, servers,
                             include_new_sfcs=False):
    """
    Tổng downtime tại 1 sự kiện re-embed.

    before_active / after_active: {request_id: Allocation}.
    Chỉ SFC đã tồn tại và bị đổi placement mới được coi là
    reconfig. SFC mới đến chỉ được tính nếu include_new_sfcs.

    Trả về (tổng downtime giây, số SFC bị reconfig có downtime > 0,
    batch_sizes dùng cho Real power).
    """
    plans = {}
    for request_id, allocation in after_active.items():
        old = before_active.get(request_id)
        if old is None and not include_new_sfcs:
            continue
        old_placement = old.placement if old else None
        if old_placement == allocation.placement:
            continue
        plans[request_id] = (old_placement, allocation.placement)

    # Batch = tất cả CNF cold start trên cùng server tại sự kiện này
    # (kể cả của SFC mới, vì chúng chia sẻ CPU của server).
    batch_sizes = Counter()
    for request_id, allocation in after_active.items():
        old = before_active.get(request_id)
        for _, server_id in cold_started_cnfs(
            old.placement if old else None,
            allocation.placement,
            servers,
        ):
            batch_sizes[server_id] += 1

    total = 0.0
    affected = 0
    for old_placement, new_placement in plans.values():
        d = sfc_reconfig_downtime(
            old_placement, new_placement, servers, batch_sizes
        )
        if d > 0:
            total += d
            affected += 1

    return total, affected, dict(batch_sizes)


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

    print(f"Cloud weight: {CLOUD_POWER_WEIGHT_W_PER_PCT:.4f} W/%")
    print(f"Edge weight : {EDGE_POWER_WEIGHT_W_PER_PCT:.5f} W/%")
    print(f"Camera power: {EDGE_CAMERA_POWER_W:.4f} W")

    _, _, batches = system_reconfig_downtime(
        {}, manager.active, servers, include_new_sfcs=True
    )

    for scenario in (SCENARIO_IDEAL, SCENARIO_REAL):
        power = computing_power(manager, scenario, batches)
        print(f"\n[{scenario}]")
        print("  Active servers:", power["active_servers"])
        print(f"  Peak cloud CPU: {power['peak_cloud_cpu_pct']:.2f}%")
        print(f"  Edge power : {power['edge_power_w']:.2f} W")
        print(f"  Cloud power: {power['cloud_power_w']:.2f} W")
        print(f"  Total      : {power['computing_power_w']:.2f} W")

    # Downtime: chuyển Den, LoS từ Edge lên Cloud.
    old = {"Cap": "edge_0_0", "Dec": "server_0",
           "Den": "edge_0_0", "LoS": "edge_0_1"}
    new = {"Cap": "edge_0_0", "Dec": "server_0",
           "Den": "server_1", "LoS": "server_1"}
    d = sfc_reconfig_downtime(old, new, servers, {"server_1": 2})
    print(f"\nDowntime Edge->Cloud (2 CNF, batch 2): {d:.2f} s")

    # Chuyển giữa các Edge -> 0 s.
    new_edge = {"Cap": "edge_0_0", "Dec": "server_0",
                "Den": "edge_0_2", "LoS": "edge_0_3"}
    d = sfc_reconfig_downtime(old, new_edge, servers)
    print(f"Downtime Edge->Edge: {d:.2f} s")


if __name__ == "__main__":
    main()
