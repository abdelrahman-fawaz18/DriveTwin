"""Assumed dependent measurements; SUMO remains the sole battery-energy owner."""

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def finite(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    return float(value)


def battery_step_power(step_wh: float, dt_s: float) -> float:
    """Use the battery device's completed-step energy, not an emission estimate."""
    finite(step_wh, "device step energy")
    finite(dt_s, "completed step")
    if dt_s <= 0:
        raise ValueError("Positive completed step required")
    return finite(step_wh * 3600 / dt_s, "converted battery power")


@dataclass(frozen=True)
class PlantConfig:
    model_version: str
    voltage_v: float
    propulsion_efficiency: float
    recuperation_efficiency: float
    non_hvac_power_w: float
    hvac_power_w: float
    wheel_radius_m: float
    final_drive_ratio: float
    torque_min_speed_rad_s: float
    pack_heat_fraction: float
    pack_capacity_j_k: float
    motor_capacity_j_k: float
    pack_conductance_w_k: float
    motor_conductance_w_k: float
    initial_pack_k: float
    initial_motor_k: float
    brake_deceleration_m_s2: float
    brake_pressure_pa: float

    @classmethod
    def load(cls, path: Path) -> "PlantConfig":
        raw = json.loads(path.read_text())
        for key in (
            "air_drag_coefficient",
            "front_area_m2",
            "rotating_mass_kg",
            "radial_drag_coefficient",
            "rolling_drag_coefficient",
        ):
            value = finite(raw["energy"][key], key)
            if value < 0 or (key == "front_area_m2" and value == 0):
                raise ValueError("Invalid energy resistance/geometry parameter")

        def number(section: str, key: str) -> float:
            return finite(raw[section][key], section + "." + key)

        result = cls(
            str(raw["model_version"]),
            number("electrical", "constant_voltage_v"),
            number("energy", "propulsion_efficiency"),
            number("energy", "recuperation_efficiency"),
            number("energy", "non_hvac_power_w"),
            number("energy", "hvac_power_w"),
            number("drivetrain", "wheel_radius_m"),
            number("drivetrain", "final_drive_ratio"),
            number("drivetrain", "torque_min_angular_speed_rad_s"),
            number("thermal", "pack_heat_fraction"),
            number("thermal", "pack_capacity_j_per_k"),
            number("thermal", "motor_capacity_j_per_k"),
            number("thermal", "pack_conductance_w_per_k"),
            number("thermal", "motor_conductance_w_per_k"),
            number("thermal", "initial_pack_temperature_k"),
            number("thermal", "initial_motor_temperature_k"),
            number("brake_proxy", "full_scale_deceleration_m_s2"),
            number("brake_proxy", "full_scale_pressure_pa"),
        )
        result.validate()
        return result

    def validate(self) -> None:
        for name, value in self.__dict__.items():
            if name == "model_version":
                if value != "synthetic-bus-plant/1.0.0":
                    raise ValueError("Unsupported dependent plant model")
                continue
            finite(value, name)
            if name in ("non_hvac_power_w", "hvac_power_w", "pack_heat_fraction"):
                if value < 0:
                    raise ValueError("Nonnegative plant parameter required")
            elif value <= 0:
                raise ValueError("Positive plant parameter required")
        if not (0 < self.propulsion_efficiency <= 1 and 0 < self.recuperation_efficiency <= 1):
            raise ValueError("Efficiency must be in (0,1]")
        if not 0 <= self.pack_heat_fraction <= 1:
            raise ValueError("Heat allocation must be in [0,1]")

    @property
    def auxiliary_power_w(self) -> float:
        return self.non_hvac_power_w + self.hvac_power_w


def temperature_step(
    old_k: float,
    ambient_k: float,
    heat_w: float,
    capacity_j_k: float,
    conductance_w_k: float,
    dt_s: float,
) -> float:
    """Exact first-order solution with inputs held over this completed step."""
    inputs = (old_k, ambient_k, heat_w, capacity_j_k, conductance_w_k, dt_s)
    for index, value in enumerate(inputs):
        finite(value, f"thermal input {index}")
    if min(old_k, ambient_k, capacity_j_k, conductance_w_k, dt_s) <= 0 or heat_w < 0:
        raise ValueError("Invalid thermal inputs")
    fraction = -math.expm1(-conductance_w_k * dt_s / capacity_j_k)
    return old_k + (ambient_k - old_k + heat_w / conductance_w_k) * fraction


class DependentPlant:
    def __init__(self, config: PlantConfig) -> None:
        config.validate()
        self.config = config
        self.pack_k, self.motor_k = config.initial_pack_k, config.initial_motor_k

    def step(
        self,
        *,
        pack_power_w: float,
        energy_j: float,
        capacity_j: float,
        speed_m_s: float,
        acceleration_m_s2: float,
        ambient_k: float,
        dt_s: float,
        charging_power_w: float = 0.0,
    ) -> dict[str, float]:
        for name, value in locals().copy().items():
            if name != "self":
                finite(value, name)
        if (
            capacity_j <= 0
            or not 0 <= energy_j <= capacity_j
            or speed_m_s < 0
            or charging_power_w < 0
        ):
            raise ValueError("Invalid bounded plant state")
        c = self.config
        # External transfer changes net battery power without driving the motor.
        drive_electrical_w = pack_power_w + charging_power_w - c.auxiliary_power_w
        mechanical_w = (
            drive_electrical_w * c.propulsion_efficiency
            if drive_electrical_w >= 0
            else drive_electrical_w / c.recuperation_efficiency
        )
        losses_w = max(0.0, drive_electrical_w - mechanical_w)
        pack_heat_w = losses_w * c.pack_heat_fraction
        self.pack_k = temperature_step(
            self.pack_k, ambient_k, pack_heat_w, c.pack_capacity_j_k, c.pack_conductance_w_k, dt_s
        )
        self.motor_k = temperature_step(
            self.motor_k,
            ambient_k,
            losses_w - pack_heat_w,
            c.motor_capacity_j_k,
            c.motor_conductance_w_k,
            dt_s,
        )
        omega = speed_m_s / c.wheel_radius_m * c.final_drive_ratio
        torque = mechanical_w / omega if omega >= c.torque_min_speed_rad_s else 0.0
        return {
            "battery.pack.soc": energy_j / capacity_j,
            "battery.pack.voltage": c.voltage_v,
            "battery.pack.current": pack_power_w / c.voltage_v,
            "battery.pack.power": pack_power_w,
            "battery.pack.energy": energy_j,
            "battery.pack.temperature": self.pack_k,
            "traction.motor.speed": omega,
            "traction.motor.torque": torque,
            "traction.motor.temperature": self.motor_k,
            "ambient.temperature": ambient_k,
            "auxiliary.power": c.auxiliary_power_w,
            "hvac.power": c.hvac_power_w,
            "braking.pressure": min(1.0, max(0.0, -acceleration_m_s2) / c.brake_deceleration_m_s2)
            * c.brake_pressure_pa,
        }


@dataclass
class EnergyLedger:
    """Check SUMO state against its rate; never integrate a competing battery."""

    previous_j: float
    capacity_j: float
    tolerance_j: float = 0.01
    steps: int = 0
    maximum_residual_j: float = 0.0
    net_consumed_j: float = 0.0
    clipped_j: float = 0.0

    def observe(self, energy_j: float, power_w: float, dt_s: float) -> None:
        for name, value in (("energy", energy_j), ("power", power_w), ("step", dt_s)):
            finite(value, name)
        if dt_s <= 0 or not 0 <= energy_j <= self.capacity_j:
            raise ValueError("Energy outside declared capacity or invalid step")
        unbounded = self.previous_j - power_w * dt_s
        expected = min(self.capacity_j, max(0.0, unbounded))
        residual = abs(energy_j - expected)
        if residual > self.tolerance_j:
            raise RuntimeError(f"SUMO energy/rate mismatch: {residual:.6g} J")
        self.maximum_residual_j = max(self.maximum_residual_j, residual)
        self.net_consumed_j += power_w * dt_s
        self.clipped_j += expected - unbounded
        self.previous_j = energy_j
        self.steps += 1
