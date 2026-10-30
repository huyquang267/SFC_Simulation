"""Cloud CNF lifecycle profiles from Table II of ICIN 2024.

x is the number of CNFs in a batch on ONE cloud server.
The published cloud calibration range is 10 to 100 CNFs.

Termination power is transcribed literally from Equation (13).
Its coefficient needs clarification before claiming an exact
reproduction of the target paper.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class LifecycleCost:
    cnf_count: int
    cpu_percent: float
    duration_s: float
    power_w: float

    @property
    def energy_j(self):
        return self.power_w * self.duration_s


def validate_count(x, allow_extrapolation=False):
    if isinstance(x, bool) or not isinstance(x, int) or x < 1:
        raise ValueError("x must be a positive integer.")

    if not 10 <= x <= 100 and not allow_extrapolation:
        raise ValueError(
            f"x={x} is outside the published cloud range [10, 100]. "
            "Using this value requires explicit extrapolation."
        )


def cloud_cold_start(x, *, allow_extrapolation=False):
    validate_count(x, allow_extrapolation)

    # Table II: Equations (1), (9), and (15).
    cpu = (
        6.12
        + 1.09 * x
        - 1.28e-2 * x**2
        + 5.28e-5 * x**3
    )

    power = 1.12e2 + 4.55e-1 * x - 2.24e-3 * x**2
    duration = 4.24 + 0.70 * x

    return LifecycleCost(x, cpu, duration, power)


def cloud_termination(x, *, allow_extrapolation=False):
    validate_count(x, allow_extrapolation)

    # Table II: Equations (5), (13), and (17).
    cpu = 2.58 + 0.20 * x

    # Equation (13) prints 1.12 × 10^-2, not 1.12 × 10^2.
    # Keep the published coefficient; do not silently change it.
    power = 1.12e-2 + 0.15 * x

    duration = 36.91 + 0.35 * x

    return LifecycleCost(x, cpu, duration, power)


def cloud_warm_profile(x, *, allow_extrapolation=False):
    validate_count(x, allow_extrapolation)

    # Table II: Equations (3), (7), and (11).
    return {
        "cpu_percent": 0.93 + 0.03 * x,
        "ram_mb": 3.36e3 + 11.24 * x,
        "power_w": 1.10e2 + 5.01e-2 * x,
    }