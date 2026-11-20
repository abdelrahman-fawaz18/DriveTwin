import unittest

from outage_session import audit_trace


def row(second, *, events=60, rejected=0):
    return {"monotonic_s": second, "database": {"events": events}, "rejected": rejected}


class ReleaseOutageTest(unittest.TestCase):
    def test_outage_samples_are_bounded_by_the_recorded_start_and_end(self):
        rows = [row(0), row(1), row(2), row(3, events=65)]
        self.assertEqual(audit_trace(rows, rows[0], rows[2]), 3)

    def test_outage_trace_rejects_missing_boundaries_gaps_ingress_and_rejections(self):
        first, last = row(0), row(3)
        for rows, error in (
            ([row(1), last], "boundaries"),
            ([first, row(6)], "coverage"),
            ([first, row(1, events=61), last], "ingress"),
            ([first, row(1, rejected=1), last], "rejected"),
        ):
            with self.subTest(error=error), self.assertRaisesRegex(RuntimeError, error):
                audit_trace(rows, first, last)
