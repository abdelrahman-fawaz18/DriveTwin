# /// script
# requires-python = ">=3.12,<3.14"
# dependencies = ["matplotlib==3.10.8"]
# ///
"""Plot the published ten-journey regression summary without new model runs."""

import re
import textwrap
from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt
from matplotlib.ticker import MultipleLocator

matplotlib.use("Agg")

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs/verification/energy-geographic.md"
OUTPUT = ROOT / "docs/engineering/figures/geographic-forecast-errors.svg"


def main():
    table = (
        SOURCE.read_text(encoding="utf-8")
        .split("| Journey | Recorded distance |", 1)[1]
        .split("\n\n", 1)[0]
    )
    rows = []
    for line in table.splitlines():
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) == 5 and re.fullmatch(r"\d+\.\d+ km", cells[1]):
            rows.append((cells[0], float(cells[4])))
    if len(rows) != 10 or max(value for _, value in rows) != 0.471:
        raise ValueError("Review the source table before updating this figure")

    with plt.rc_context(
        {
            "font.family": "DejaVu Sans",
            "font.size": 11,
            "text.color": "#10263b",
            "axes.labelcolor": "#456078",
            "xtick.color": "#456078",
            "ytick.color": "#10263b",
            "svg.fonttype": "none",
            "svg.hashsalt": "drivetwin-geographic-regression",
        }
    ):
        fig, ax = plt.subplots(figsize=(12.4, 7.7), facecolor="white")
        fig.subplots_adjust(left=0.39, right=0.94, top=0.80, bottom=0.16)
        values = [value for _, value in rows]
        ax.barh(range(len(rows)), values, height=0.57, color="#087f8c", zorder=3)
        ax.set_yticks(
            range(len(rows)),
            [
                textwrap.fill(name, width=37, break_long_words=False, break_on_hyphens=False)
                for name, _ in rows
            ],
        )
        ax.invert_yaxis()
        ax.set_xlim(0, 0.52)
        ax.xaxis.set_major_locator(MultipleLocator(0.1))
        ax.set_xlabel("Maximum absolute arrival-charge error (percentage points)", labelpad=15)
        ax.grid(axis="x", color="#e2e9ef", linewidth=0.8, zorder=0)
        ax.tick_params(axis="both", length=0, pad=9)
        for spine in ax.spines.values():
            spine.set_visible(False)
        for y, value in enumerate(values):
            ax.text(
                value + 0.008,
                y,
                f"{value:.3f}",
                va="center",
                fontsize=11,
                color="#10263b",
                fontweight="bold",
            )
        fig.text(
            0.035, 0.935, "Arrival-charge error across ten routes", fontsize=23, fontweight="bold"
        )
        fig.text(
            0.035,
            0.888,
            "70 forecasts compared with SUMO battery readings · lower error is better",
            fontsize=12,
            color="#456078",
        )
        fig.text(
            0.035,
            0.065,
            "Final regression batch · real OSM roads and attributed elevation · no route-specific training",
            fontsize=10,
            color="#456078",
        )
        fig.text(
            0.035,
            0.035,
            "Source: docs/verification/energy-geographic.md · values rounded as reported",
            fontsize=9,
            color="#456078",
        )
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(
            OUTPUT,
            metadata={
                "Date": None,
                "Creator": "DriveTwin",
                "Title": "Maximum arrival-charge forecast error across ten geographic journeys",
                "Description": "Final 70-forecast regression batch against SUMO battery readings. "
                "Each bar is the largest absolute arrival-charge error for one journey, "
                "in percentage points. Values range from 0.010 to 0.471. "
                "This figure restates the published summary; it is not a new evaluation.",
            },
        )
        OUTPUT.write_bytes(
            b"\n".join(line.rstrip() for line in OUTPUT.read_bytes().splitlines()) + b"\n"
        )
        plt.close(fig)
    print(f"Wrote {OUTPUT.relative_to(ROOT).as_posix()} from 10 published results")


if __name__ == "__main__":
    main()
