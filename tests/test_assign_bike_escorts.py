import unittest

from assign_bike_escorts import BikeEscort, FixedRange, SegmentRow, assign_bikers


class BikeEscortPostGenTests(unittest.TestCase):
    def test_assigns_night_first_with_priority(self) -> None:
        segments = [
            SegmentRow(seg_id=1, km=2.0, row_idx=0, biker="", is_night=True),
            SegmentRow(seg_id=2, km=2.0, row_idx=1, biker="", is_night=True),
            SegmentRow(seg_id=3, km=2.0, row_idx=2, biker="", is_night=False),
            SegmentRow(seg_id=4, km=2.0, row_idx=3, biker="", is_night=False),
        ]
        escorts = [
            BikeEscort(name="Regi", target_km=2.0),
            BikeEscort(name="Lilla", target_km=2.0),
            BikeEscort(name="Bianka", target_km=2.0),
        ]
        owner_by_segment, summary = assign_bikers(
            segments=segments,
            escorts=escorts,
            fixed_ranges=[],
            priority=["Regi", "Lilla", "Bianka"],
            fill_day_segments=True,
            write_into_empty_only=True,
        )

        self.assertEqual(owner_by_segment[1], "Regi")
        self.assertEqual(owner_by_segment[2], "Lilla")
        self.assertEqual(owner_by_segment[3], "Bianka")
        self.assertNotIn(4, owner_by_segment)
        self.assertEqual(summary["Regi"]["night_km"], 2.0)
        self.assertEqual(summary["Lilla"]["night_km"], 2.0)
        self.assertEqual(summary["Bianka"]["night_km"], 0.0)

    def test_fixed_ranges_first_wins_and_day_only(self) -> None:
        segments = [
            SegmentRow(seg_id=17, km=2.0, row_idx=0, biker="", is_night=False),
            SegmentRow(seg_id=18, km=2.0, row_idx=1, biker="", is_night=False),
            SegmentRow(seg_id=19, km=2.0, row_idx=2, biker="", is_night=True),
            SegmentRow(seg_id=28, km=2.0, row_idx=3, biker="", is_night=True),
            SegmentRow(seg_id=29, km=2.0, row_idx=4, biker="", is_night=True),
        ]
        owner_by_segment, summary = assign_bikers(
            segments=segments,
            escorts=[],
            fixed_ranges=[
                FixedRange(name="Brigi", start_seg=1, end_seg=18, day_only=True),
                FixedRange(name="Máté", start_seg=18, end_seg=28, day_only=False),
                FixedRange(name="Lajek", start_seg=28, end_seg=44, day_only=False),
            ],
            priority=[],
            fill_day_segments=False,
            write_into_empty_only=False,
        )
        self.assertEqual(owner_by_segment[17], "Brigi")
        self.assertEqual(owner_by_segment[18], "Brigi")
        self.assertEqual(owner_by_segment[19], "Máté")
        self.assertEqual(owner_by_segment[28], "Máté")
        self.assertEqual(owner_by_segment[29], "Lajek")
        self.assertEqual(summary["Brigi"]["target_km"], None)
        self.assertEqual(summary["Máté"]["target_km"], None)


if __name__ == "__main__":
    unittest.main()
