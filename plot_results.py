import csv
from pathlib import Path

import matplotlib.pyplot as plt


INPUT_FILE = Path("experiment_hourly_results.csv")
OUTPUT_FILE = Path("validation_results.png")

STRATEGIES = [
    "Cloud",
    "Edge-cloud",
    "Edge-cloud + EO",
]

STYLES = {
    "Cloud": {
        "color": "tab:blue",
        "marker": "o",
        "linestyle": "-",
    },
    "Edge-cloud": {
        "color": "tab:orange",
        "marker": "s",
        "linestyle": "--",
    },
    "Edge-cloud + EO": {
        "color": "tab:green",
        "marker": "^",
        "linestyle": ":",
    },
}


def main():
    if not INPUT_FILE.exists():
        raise FileNotFoundError(
            f"Không tìm thấy {INPUT_FILE}"
        )

    with INPUT_FILE.open(
        encoding="utf-8-sig",
        newline="",
    ) as file:
        rows = [
            row
            for row in csv.DictReader(file)
            if row["phase"] == "arrivals"
        ]

    if not rows:
        raise ValueError(
            "CSV không có dòng phase=arrivals."
        )

    seeds = {row["seed"] for row in rows}

    if len(seeds) != 1:
        raise ValueError(
            "File này dành cho kết quả một seed. "
            "Nhiều seed cần tổng hợp trước khi vẽ."
        )

    fig, axes = plt.subplots(
        1,
        2,
        figsize=(12, 4.5),
        constrained_layout=True,
    )

    for strategy in STRATEGIES:
        selected = sorted(
            (
                row
                for row in rows
                if row["strategy"] == strategy
            ),
            key=lambda row: int(row["hour"]),
        )

        if not selected:
            continue

        arrival_rates = [
            float(row["configured_arrivals_per_hour"])
            for row in selected
        ]

        acceptance = [
            (
                100.0 * float(row["acceptance_ratio"])
                if row["acceptance_ratio"]
                else float("nan")
            )
            for row in selected
        ]

        utilization = [
            100.0 * float(row["average_utilization"])
            for row in selected
        ]

        style = STYLES[strategy]

        for axis, values in zip(
            axes,
            (acceptance, utilization),
        ):
            axis.plot(
                arrival_rates,
                values,
                label=strategy,
                linewidth=2,
                markersize=9,
                markerfacecolor="none",
                **style,
            )

    axes[0].set_title("Acceptance")
    axes[0].set_ylabel("Accepted requests (%)")
    axes[0].set_ylim(0, 105)

    axes[1].set_title("Average utilization")
    axes[1].set_ylabel("Average active SFCs / 2100 (%)")
    axes[1].set_ylim(bottom=0)

    for axis in axes:
        axis.set_xlabel(
            "Configured arrival rate (requests/hour)"
        )
        axis.grid(True, alpha=0.3)
        axis.legend()

    seed = next(iter(seeds))

    fig.suptitle(
        f"Implementation validation — seed {seed}"
    )

    fig.savefig(OUTPUT_FILE, dpi=200)

    print(f"Saved: {OUTPUT_FILE.resolve()}")

    plt.show()


if __name__ == "__main__":
    main()