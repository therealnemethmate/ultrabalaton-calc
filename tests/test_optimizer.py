import unittest

from optimizer import Runner, Segment, SolverConfig, solve_runner_assignment, to_contiguous_ranges


class OptimizerTests(unittest.TestCase):
    def test_to_contiguous_ranges(self) -> None:
        segments = [Segment(id=i + 1, km=1.0) for i in range(8)]
        ranges = to_contiguous_ranges([0, 1, 2, 5, 6], segments)
        self.assertEqual(
            ranges,
            [
                {
                    "start_index": 0,
                    "end_index": 2,
                    "start_segment_id": 1,
                    "end_segment_id": 3,
                },
                {
                    "start_index": 5,
                    "end_index": 6,
                    "start_segment_id": 6,
                    "end_segment_id": 7,
                },
            ],
        )

    def test_solver_finds_feasible_solution(self) -> None:
        segments = [
            Segment(id=1, km=2.0),
            Segment(id=2, km=2.0),
            Segment(id=3, km=2.0),
            Segment(id=4, km=2.0),
        ]
        runners = [
            Runner(name="A", target_km=4.0, min_blocks=1, max_blocks=1, car_id="1"),
            Runner(name="B", target_km=4.0, min_blocks=1, max_blocks=1, car_id="1"),
        ]
        result = solve_runner_assignment(
            segments=segments,
            runners=runners,
            current_owner={1: "A", 2: "A", 3: "B", 4: "B"},
            config=SolverConfig(
                time_limit_sec=5,
                max_overflow_km=0.0,
                require_every_runner_used=True,
            ),
        )
        self.assertIn(result["status"], {"OPTIMAL", "FEASIBLE"})
        self.assertEqual(len(result["segment_owner"]), 4)
        for runner in result["runners"]:
            self.assertLessEqual(runner["block_count"], runner["max_blocks"])


if __name__ == "__main__":
    unittest.main()
