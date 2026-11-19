"""설계강우(Huff) 생성, LID 혼합 배치, 1 ha당 저감량, 지도 이름표 위치 시험."""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from swmm_lid import InpFile, LidConfig, ScenarioConfig, compare_to_baseline, load_model, run_scenario  # noqa: E402
from swmm_lid.rain import DISTRIBUTIONS, HUFF_POINT_MEDIAN, design_hyetograph  # noqa: E402
from swmm_lid.scenario import gauge_interval_min  # noqa: E402

EX2 = os.path.join(ROOT, "models", "Example2-Post.inp")


class RainTests(unittest.TestCase):
    def test_huff_curves_monotonic(self):
        for q, c in HUFF_POINT_MEDIAN.items():
            self.assertEqual(c[0], 0)
            self.assertEqual(c[-1], 100)
            self.assertTrue(all(b >= a for a, b in zip(c, c[1:])), q)

    def test_hyetograph_total_depth(self):
        for name in DISTRIBUTIONS:
            h = design_hyetograph(80.0, 120.0, 5.0, name)
            self.assertEqual(len(h), 24)
            self.assertAlmostEqual(sum(h) * 5 / 60, 80.0, places=6)

    def test_quartile_peak_position(self):
        # 1분위는 앞쪽, 4분위는 뒤쪽에 최대 강도
        h1 = design_hyetograph(50, 120, 5, "Huff 1분위")
        h4 = design_hyetograph(50, 120, 5, "Huff 4분위")
        self.assertLess(h1.index(max(h1)), 6)
        self.assertGreater(h4.index(max(h4)), 17)


@unittest.skipUnless(os.path.exists(EX2), "Example2-Post.inp 없음")
class MixedLidTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = load_model(InpFile.from_file(EX2))

    def test_mixed_assignment_runs(self):
        assign = {"S1": ["BC", 50], "S2": ["IT", 70], "S5": ["PP", 40], "S7": ["BC", 50]}
        base = run_scenario(self.model, ScenarioConfig("10-yr"))
        r = run_scenario(self.model, ScenarioConfig("10-yr", LidConfig(True, assign=assign)))
        got = {p.subcatchment: p.lid_type for p in r.placements}
        self.assertEqual(got, {"S1": "BC", "S2": "IT", "S5": "PP"})      # S7은 불투수 0% → 제외
        for t in ("LID_BC", "LID_IT", "LID_PP"):
            self.assertIn(t, r.inp_text)
        self.assertEqual(len(r.report.lid_performance), 3)
        for row in r.report.lid_performance:
            self.assertLess(abs(row["continuity_error"]), 1.0)
        c = compare_to_baseline(r, base)
        self.assertGreater(c["volume_reduction_pct"], 0)
        self.assertGreater(c["vol_red_m3_per_ha"], 0)
        self.assertIn("혼합", r.label)

    def test_mixed_equals_simple_when_same_type(self):
        subs = ["S1", "S2"]
        a = run_scenario(self.model, ScenarioConfig("10-yr", LidConfig(True, "BC", 50, None, subs)))
        b = run_scenario(self.model, ScenarioConfig("10-yr", LidConfig(True, assign={s: ["BC", 50] for s in subs})))
        self.assertAlmostEqual(a.kpi["peak_flow_cms"], b.kpi["peak_flow_cms"], places=6)

    def test_huff_storm_runs(self):
        h = design_hyetograph(80, 120, gauge_interval_min(self.model), "Huff 3분위")
        r = run_scenario(self.model, ScenarioConfig("Huff", custom_rain=h))
        self.assertAlmostEqual(r.kpi["precip_mm"], 80.0, delta=0.1)
        self.assertLess(abs(r.kpi["runoff_error_pct"]), 1.0)


class LabelTests(unittest.TestCase):
    def test_label_inside_concave_polygon(self):
        sys.path.insert(0, os.path.join(ROOT, "tests", "stubs"))
        from swmm_lid.charts import _inside, label_point
        L = [(0, 0), (10, 0), (10, 2), (2, 2), (2, 10), (0, 10)]        # ㄴ자
        x, y = label_point(L)
        self.assertTrue(_inside(x, y, L))


if __name__ == "__main__":
    unittest.main()
