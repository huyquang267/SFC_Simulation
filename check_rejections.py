from collections import Counter
from pathlib import Path


def main():
    files = [
        "experiment_cloud.log",
        "experiment_edge_cloud.log",
        "experiment_edge_cloud_eo.log",
    ]

    for filename in files:
        path = Path(filename)

        print(f"\n===== {filename} =====")

        if not path.exists():
            print("Không tìm thấy file.")
            continue

        reasons = Counter()

        with path.open(encoding="utf-8") as file:
            for line in file:
                if "REJECTED" not in line:
                    continue

                reason = line.partition("|")[2].strip()
                reasons[reason or "unknown"] += 1

        print(f"Total rejected: {sum(reasons.values())}")

        for reason, count in reasons.most_common():
            print(f"  {count}: {reason}")


if __name__ == "__main__":
    main()