import math
import unittest
from dataclasses import replace

from evdt.energy.route_aware.contracts import RoadPoint
from evdt.energy.route_aware.geometry import horizontal_length
from evdt.energy.route_aware.sumo_routes import NetworkLane, _at_fraction, _lane_change


class AdjacentLaneAlignmentTests(unittest.TestCase):
    def lane(self, index, inset):
        radius = 100 - inset
        shape = (
            RoadPoint(0, inset, 20),
            *(
                RoadPoint(
                    1000 + radius * math.cos(-math.pi / 2 + i * math.pi / 80),
                    100 + radius * math.sin(-math.pi / 2 + i * math.pi / 80),
                    20,
                )
                for i in range(81)
            ),
            RoadPoint(0, 200 - inset, 20),
        )
        return NetworkLane(
            f"curve_{index}",
            "curve",
            False,
            "urban",
            shape,
            horizontal_length(shape),
            15,
            index,
            True,
            True,
        )

    def test_long_parallel_curve_matches_local_carriageway_without_widening_gap_limit(self):
        entry, exit_lane = self.lane(0, 0), self.lane(1, 3.2)
        a, b = _at_fraction(entry.shape, 0.43), _at_fraction(exit_lane.shape, 0.43)
        self.assertGreater(math.hypot(a.x_m - b.x_m, a.y_m - b.y_m), 5)
        result = _lane_change(entry, exit_lane)
        self.assertIsNotNone(result)
        self.assertEqual(result.shape[0], entry.shape[0])
        self.assertAlmostEqual(result.shape[-1].x_m, exit_lane.shape[-1].x_m, places=8)
        self.assertAlmostEqual(result.shape[-1].y_m, exit_lane.shape[-1].y_m, places=8)
        self.assertLess(abs(horizontal_length(result.shape) - entry.length_m), 15)
        self.assertIsNone(_lane_change(entry, self.lane(1, 6)))

    def test_lane_permissions_and_adjacency_remain_required(self):
        entry, exit_lane = self.lane(0, 0), self.lane(1, 3.2)
        self.assertIsNone(_lane_change(replace(entry, change_left=False), exit_lane))
        self.assertIsNone(_lane_change(entry, replace(exit_lane, index=2)))
