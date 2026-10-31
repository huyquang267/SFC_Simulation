"""
Vẽ 4 hình giống bài báo từ paper_results.csv:
Fig. 5 Acceptance, Fig. 6 Power vs Load,
Fig. 7 Power vs Utilization, Fig. 8 Downtime.

Nhiều seed -> lấy trung bình theo (strategy, hour).
"""

import argparse
import csv
from collections import defaultdict
from statistics import mean

import matplotlib.pyplot as plt


STRATEGIES = ["Cloud", "Edge-cloud", "Edge-cloud + EO"]
LABELS = {"Cloud": "Cloud", "Edge-cloud": "Edge-cloud",
          "Edge-cloud + EO": "Edge-cloud+EO"}

# Màu/marker theo bài báo.
REAL_STYLE = {
    "Cloud": dict(color="#bcbd22", marker="^"),
    "Edge-cloud": dict(color="#c71585", marker=">"),
    "Edge-cloud + EO": dict(color="#17becf", marker="s"),
}
IDEAL_STYLE = {
    "Cloud": dict(color="blue", marker="*"),
    "Edge-cloud": dict(color="red", marker="v"),
    "Edge-cloud + EO": dict(color="green", marker="o"),
}

FIELDS = ["offered_load_pct", "acceptance_ratio", "average_utilization",
          "ideal_power_kw", "real_power_kw", "downtime_s"]


def load(filename):
    groups = defaultdict(list)
    with open(filename, encoding="utf-8-sig", newline="") as file:
        for row in csv.DictReader(file):
            if row["phase"] == "arrivals":
                groups[(row["strategy"], int(row["hour"]))].append(row)

    data = defaultdict(lambda: defaultdict(list))
    for (strategy, hour), rows in sorted(groups.items()):
        for field in FIELDS:
            values = [float(r[field]) for r in rows if r[field] != ""]
            data[strategy][field].append(mean(values) if values else float("nan"))
    return data


def plot(ax, x, y, style, label):
    ax.plot(x, y, label=label, linewidth=1.5, markersize=7, **style)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="paper_results.csv")
    parser.add_argument("--output", default="paper_figures.png")
    args = parser.parse_args()

    data = load(args.input)
    fig, axes = plt.subplots(2, 2, figsize=(12, 9), constrained_layout=True)
    ax5, ax7, ax6, ax8 = axes.flat

    for s in STRATEGIES:
        if s not in data:
            continue
        d = data[s]
        load_pct = d["offered_load_pct"]
        util_pct = [100 * u for u in d["average_utilization"]]

        plot(ax5, load_pct, d["acceptance_ratio"], REAL_STYLE[s], LABELS[s])

        plot(ax6, load_pct, d["real_power_kw"], REAL_STYLE[s], LABELS[s])
        plot(ax6, load_pct, d["ideal_power_kw"], IDEAL_STYLE[s],
             LABELS[s] + " Ideal")

        plot(ax7, util_pct, d["real_power_kw"], REAL_STYLE[s], LABELS[s])
        plot(ax7, util_pct, d["ideal_power_kw"], IDEAL_STYLE[s],
             LABELS[s] + " Ideal")

        plot(ax8, load_pct, d["downtime_s"], REAL_STYLE[s], LABELS[s])

    ax5.set(title="Fig. 5: Acceptance ratio", xlabel="Offered Load (%)",
            ylabel="Acceptance Ratio")
    ax6.set(title="Fig. 6: System power vs offered load",
            xlabel="Offered Load (%)", ylabel="System power (kW)")
    ax7.set(title="Fig. 7: System power vs utilization",
            xlabel="Utilization (%)", ylabel="System power (kW)")
    ax8.set(title="Fig. 8: Hourly downtime", xlabel="Offered Load (%)",
            ylabel="Downtime (s)")
    ax8.ticklabel_format(axis="y", style="sci", scilimits=(2, 2))

    for ax in axes.flat:
        ax.grid(True, alpha=0.4)
        ax.legend(fontsize=8)

    fig.savefig(args.output, dpi=200)
    print(f"Saved: {args.output}")


if __name__ == "__main__":
    main()
