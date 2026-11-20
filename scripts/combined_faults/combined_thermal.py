"""Bounded thermal subsystem for the isolated Phase 6 simulator fixture."""

from dataclasses import dataclass

from evdt.monitoring.plant import temperature_step


@dataclass(frozen=True)
class Temperatures:
    motor: float
    housing: float

    def step(self, policy, *, degraded, boost_w_k):
        coupling = policy["coupling_w_k"]
        ambient = policy["ambient_k"]
        housing_conductance = (
            policy["degraded_housing_w_k"] if degraded else policy["healthy_housing_w_k"]
        ) + boost_w_k
        motor, housing = self.motor, self.housing
        # Hold the adjacent node constant for each 0.2-second substep.
        for _ in range(5):
            motor, housing = (
                temperature_step(
                    motor, housing, policy["heat_w"][0], policy["capacity_j_k"][0], coupling, 0.2
                ),
                temperature_step(
                    housing,
                    (coupling * motor + housing_conductance * ambient)
                    / (coupling + housing_conductance),
                    policy["heat_w"][1],
                    policy["capacity_j_k"][1],
                    coupling + housing_conductance,
                    0.2,
                ),
            )
        return Temperatures(motor, housing)
