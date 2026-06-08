import unittest

from arena_coach.gui.widgets.category_radar_widget import (
    category_scores_from_breakdown,
    grade_for_score,
    overall_score_from_scores,
)


class RadarWidgetHelperTests(unittest.TestCase):
    def test_grade_thresholds(self):
        self.assertEqual(grade_for_score(95.0), "S")
        self.assertEqual(grade_for_score(90.0), "S")
        self.assertEqual(grade_for_score(89.9), "A")
        self.assertEqual(grade_for_score(75.0), "A")
        self.assertEqual(grade_for_score(74.9), "B")
        self.assertEqual(grade_for_score(55.0), "B")
        self.assertEqual(grade_for_score(54.9), "C")
        self.assertEqual(grade_for_score(40.0), "C")
        self.assertEqual(grade_for_score(39.9), "D")
        self.assertEqual(grade_for_score(20.0), "D")
        self.assertEqual(grade_for_score(19.9), "F")
        self.assertEqual(grade_for_score(None), "-")

    def test_category_score_extraction_and_overall_average(self):
        scores = category_scores_from_breakdown(
            {
                "shooting": {"overall_score": 64.8},
                "speed": {"overall_score": 55.5},
                "possession": {"overall_score": 48.5},
                "offense": {"overall_score": 68.2},
                "defense": {"overall_score": 52.1},
                "passing": {"overall_score": 64.0},
            }
        )
        self.assertEqual(scores["shooting"], 64.8)
        self.assertEqual(scores["speed"], 55.5)
        self.assertEqual(scores["passing"], 64.0)
        self.assertAlmostEqual(overall_score_from_scores(scores), 58.85)

    def test_missing_scores_are_ignored_in_overall_average(self):
        scores = category_scores_from_breakdown(
            {
                "shooting": {"overall_score": 80.0},
                "speed": {"overall_score": 60.0},
            }
        )
        self.assertIsNone(scores["defense"])
        self.assertAlmostEqual(overall_score_from_scores(scores), 70.0)


if __name__ == "__main__":
    unittest.main()
