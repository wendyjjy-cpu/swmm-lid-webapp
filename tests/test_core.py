"""계산 모듈 자동 시험.  실행: python -m unittest discover -s tests -v

SWMM 엔진이 필요하다 (swmm-toolkit 설치 또는 SWMM_LIB/SWMM_EXE 환경변수).
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from swmm_lid import InpFile, LidConfig, ScenarioConfig, compare_to_baseline, load_model, run_scenario  # noqa: E402
from swmm_lid.lid import plan_placements  # noqa: E402

MODEL_PATH = os.environ.get("TEST_INP", os.path.join(ROOT, "tests", "data", "test_site.inp"))


class CoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = load_model(InpFile.from_file(MODEL_PATH))
        cls.storms = cls.model.rain_series_names()
        cls.subs = list(cls.model.subcatchments)

    def test_inp_roundtrip_preserves_text(self):
        with open(MODEL_PATH, encoding="utf-8") as f:
            text = f.read()
        self.assertEqual(InpFile(text).to_text().strip(), text.strip())

    def test_baseline_continuity(self):
        for s in self.storms:
            r = run_scenario(self.model, ScenarioConfig(s))
            self.assertLess(abs(r.kpi["runoff_error_pct"]), 1.0)
            self.assertLess(abs(r.kpi["routing_error_pct"]), 2.0)
            self.assertGreater(r.kpi["peak_flow_cms"], 0)

    def test_out_matches_report_volume(self):
        r = run_scenario(self.model, ScenarioConfig(self.storms[0]))
        # 계통 방류량 적분(보고 간격) vs 리포트 총유출량: 1% 이내
        dt_s = (r.minutes[1] - r.minutes[0]) * 60
        vol = float(r.outflow_cms.sum() * dt_s)
        self.assertAlmostEqual(vol / r.kpi["outflow_vol_m3"], 1.0, delta=0.02)

    def test_lid_reduces_runoff_and_balances(self):
        for t in ("BC", "PP", "IT"):
            base = run_scenario(self.model, ScenarioConfig(self.storms[0]))
            r = run_scenario(self.model, ScenarioConfig(self.storms[0], LidConfig(True, t, 50, None, self.subs)))
            c = compare_to_baseline(r, base)
            self.assertGreater(c["volume_reduction_pct"], 0, t)
            self.assertGreaterEqual(c["peak_reduction_pct"], -0.5, t)
            self.assertTrue(r.report.lid_performance, t)
            for row in r.report.lid_performance:
                self.assertLess(abs(row["continuity_error"]), 1.0)

    def test_clogging_never_improves_volume_reduction(self):
        base = run_scenario(self.model, ScenarioConfig(self.storms[0]))
        prev = None
        for clog in (0, 75, 95):
            r = run_scenario(self.model, ScenarioConfig(self.storms[0],
                                                        LidConfig(True, "BC", 50, None, self.subs, clog)))
            red = compare_to_baseline(r, base)["volume_reduction_pct"]
            if prev is not None:
                self.assertLessEqual(red, prev + 0.05)
            prev = red

    def test_area_bookkeeping(self):
        cfg = LidConfig(True, "BC", 50, None, self.subs)
        for p in plan_placements(self.model, cfg):
            sc = self.model.subcatchments[p.subcatchment]
            imp_before = sc.area * sc.imperv / 100
            imp_after = (sc.area - p.lid_area_model) * p.new_imperv_pct / 100
            self.assertAlmostEqual(imp_before, imp_after, places=6)   # 투수면에 설치 → 불투수면적 보존
        cfg = LidConfig(True, "PP", 50, None, self.subs)
        for p in plan_placements(self.model, cfg):
            sc = self.model.subcatchments[p.subcatchment]
            imp_before = sc.area * sc.imperv / 100
            imp_after = (sc.area - p.lid_area_model) * p.new_imperv_pct / 100
            self.assertAlmostEqual(imp_before - p.lid_area_model, imp_after, places=6)  # 포장 대체

    def test_no_lid_when_no_targets(self):
        r = run_scenario(self.model, ScenarioConfig(self.storms[0], LidConfig(True, "BC", 50, None, [])))
        self.assertEqual(r.placements, [])
        self.assertNotIn("[LID_USAGE]", r.inp_text)


if __name__ == "__main__":
    unittest.main()
