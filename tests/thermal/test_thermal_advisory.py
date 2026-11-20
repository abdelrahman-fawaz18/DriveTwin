import unittest

from scripts.thermal.thermal_advisory import Reading, Sample, ThermalAdvisory

RULES = {
    "maximum_age_s": 5,
    "sensor_step_k": 3,
    "max_positive_hot_case_disagreement_k": 3.8,
    "hot_residual_k": 4,
    "case_residual_k": 3,
    "persistence_s": 3,
    "clear_residual_k": 2,
    "clear_persistence_s": 3,
}


def sample(t, hot=334, case=313, observed=None, housing_stamp=None, expected=330):
    return Sample(
        t,
        None if hot is None else Reading(hot, t if observed is None else observed),
        None if case is None else Reading(case, t if housing_stamp is None else housing_stamp),
        expected,
        310,
    )


class ThermalAdvisoryTest(unittest.TestCase):
    def test_threshold_and_elapsed_boundary(self):
        monitor = ThermalAdvisory(RULES, 0)
        self.assertEqual(
            [monitor.step(sample(t)).state for t in range(4)],
            ["normal", "normal", "normal", "cooling_consistent"],
        )

    def test_single_sensor_can_only_warn_ambiguously(self):
        monitor = ThermalAdvisory(RULES, 0)
        self.assertEqual(
            [monitor.step(sample(t, case=None)).state for t in range(4)][-1], "ambiguous"
        )

    def test_disagreement_cannot_corroborate(self):
        monitor = ThermalAdvisory(RULES, 0)
        states = [monitor.step(sample(t, hot=337, case=313)).state for t in range(5)]
        self.assertEqual(states[-1], "ambiguous")

    def test_missing_and_repeated_input_reset_persistence(self):
        for fault in (sample(3, hot=None), sample(3, observed=2)):
            monitor = ThermalAdvisory(RULES, 0)
            for t in range(3):
                monitor.step(sample(t))
            self.assertEqual(monitor.step(fault).state, "data_unavailable")
            states = [monitor.step(sample(t)).state for t in range(4, 8)]
            self.assertEqual(states, ["normal", "normal", "normal", "cooling_consistent"])

    def test_age_boundary_and_future_sample(self):
        self.assertEqual(ThermalAdvisory(RULES, 0).step(sample(5, observed=0)).state, "normal")
        self.assertEqual(ThermalAdvisory(RULES, 0).step(sample(6, observed=0)).reason, "stale")
        self.assertEqual(ThermalAdvisory(RULES, 0).step(sample(0, observed=1)).reason, "future")

    def test_gap_breaks_evidence_and_step_comparison(self):
        monitor = ThermalAdvisory(RULES, 0)
        for t in range(3):
            monitor.step(sample(t))
        self.assertEqual(monitor.step(sample(5, hot=339)).state, "normal")
        self.assertEqual(monitor.step(sample(6, hot=339)).state, "normal")

    def test_step_latches_sensor_suspicion_across_dropout(self):
        monitor = ThermalAdvisory(RULES, 0)
        monitor.step(sample(0))
        self.assertEqual(monitor.step(sample(1, hot=337)).state, "sensor_suspect")
        self.assertEqual(monitor.step(sample(2, hot=None)).state, "data_unavailable")
        self.assertEqual(monitor.step(sample(3, hot=337)).state, "sensor_suspect")

    def test_clear_requires_full_window(self):
        monitor = ThermalAdvisory(RULES, 0)
        for t in range(4):
            monitor.step(sample(t))
        self.assertEqual(monitor.step(sample(4, hot=333)).state, "ambiguous")
        states = [monitor.step(sample(t, hot=332)).state for t in range(5, 9)]
        self.assertEqual(states, ["ambiguous", "ambiguous", "ambiguous", "normal"])

    def test_unsynchronized_housing_and_invalid_prediction(self):
        monitor = ThermalAdvisory(RULES, 0)
        states = [monitor.step(sample(t, housing_stamp=t - 1)).state for t in range(5)]
        self.assertEqual(states[-1], "ambiguous")
        self.assertEqual(
            monitor.step(sample(5, expected=float("nan"))).reason, "prediction_unavailable"
        )


if __name__ == "__main__":
    unittest.main()
