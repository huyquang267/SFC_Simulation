import heapq
import math
import random

from dataclasses import dataclass

from config import MEAN_SERVICE_TIME_S
from topology import build_topology


@dataclass(frozen=True)
class SFCRequest:
    request_id: int
    camera_id: str
    source_device: str
    arrival_time: float
    service_time: float

    @property
    def departure_time(self):
        return self.arrival_time + self.service_time


def generate_requests(
    camera_sources,
    arrivals_per_hour,
    seed,
    return_stats=False,
):
    if not camera_sources:
        raise ValueError("Danh sách camera đang trống.")

    if (
        not math.isfinite(MEAN_SERVICE_TIME_S)
        or MEAN_SERVICE_TIME_S <= 0
    ):
        raise ValueError(
            "Thời gian phục vụ trung bình phải dương."
        )

    rates = list(arrivals_per_hour)

    for rate in rates:
        if not math.isfinite(rate) or rate < 0:
            raise ValueError(
                "Tốc độ arrival phải hữu hạn và không âm."
            )

    rng = random.Random(seed)

    # Camera rảnh để tạo một phiên yêu cầu mới.
    available_cameras = sorted(camera_sources)

    # Heap chứa:
    # (thời điểm kết thúc phiên, camera_id).
    busy_cameras = []

    requests = []
    statistics = []

    for hour, rate_per_hour in enumerate(rates):
        start_time = hour * 3600.0
        end_time = start_time + 3600.0

        record = {
            "hour": hour + 1,
            "configured_arrivals_per_hour": rate_per_hour,
            "arrival_attempts": 0,
            "generated_requests": 0,
            "no_free_camera": 0,
        }

        statistics.append(record)

        if rate_per_hour == 0:
            continue

        current_time = start_time
        rate_per_second = rate_per_hour / 3600.0

        while True:
            current_time += rng.expovariate(
                rate_per_second
            )

            if current_time >= end_time:
                break

            record["arrival_attempts"] += 1

            # Trả lại những camera có phiên đã kết thúc.
            while (
                busy_cameras
                and busy_cameras[0][0] <= current_time
            ):
                _, camera_id = heapq.heappop(
                    busy_cameras
                )
                available_cameras.append(camera_id)

            # Nếu tất cả camera đều có phiên đang hoạt động,
            # không tạo thêm SFC cho cùng một camera.
            if not available_cameras:
                record["no_free_camera"] += 1
                continue

            # Chọn đều trong tập camera đang rảnh.
            camera_index = rng.randrange(
                len(available_cameras)
            )

            camera_id = available_cameras[
                camera_index
            ]

            # Loại camera vừa chọn khỏi danh sách rảnh.
            available_cameras[camera_index] = (
                available_cameras[-1]
            )
            available_cameras.pop()

            service_time = rng.expovariate(
                1.0 / MEAN_SERVICE_TIME_S
            )

            request = SFCRequest(
                request_id=len(requests),
                camera_id=camera_id,
                source_device=camera_sources[camera_id],
                arrival_time=current_time,
                service_time=service_time,
            )

            requests.append(request)

            heapq.heappush(
                busy_cameras,
                (
                    request.departure_time,
                    camera_id,
                ),
            )

            record["generated_requests"] += 1

    if return_stats:
        return requests, statistics

    return requests


def check_camera_overlap(requests):
    """
    Kiểm tra các phiên của cùng camera không chồng nhau.
    """
    previous_departure = {}

    for request in sorted(
        requests,
        key=lambda item: item.arrival_time,
    ):
        camera_id = request.camera_id

        if request.arrival_time < (
            previous_departure.get(
                camera_id,
                float("-inf"),
            )
        ):
            raise RuntimeError(
                f"Phiên bị trùng ở camera {camera_id}"
            )

        previous_departure[camera_id] = (
            request.departure_time
        )


def main():
    _, _, _, cameras = build_topology()

    requests, statistics = generate_requests(
        camera_sources=cameras,
        arrivals_per_hour=[210, 420],
        seed=0,
        return_stats=True,
    )

    check_camera_overlap(requests)

    print("Camera overlap check: PASS")
    print(f"Generated requests: {len(requests)}")

    for record in statistics:
        print(
            f"Hour {record['hour']}: "
            f"attempts={record['arrival_attempts']}, "
            f"generated={record['generated_requests']}, "
            f"no_free_camera={record['no_free_camera']}"
        )

    print("\nFirst 5 requests:")

    for request in requests[:5]:
        print(
            f"SFC {request.request_id}: "
            f"camera={request.camera_id}, "
            f"source={request.source_device}, "
            f"arrival={request.arrival_time:.2f}s, "
            f"duration={request.service_time:.2f}s"
        )


if __name__ == "__main__":
    main()