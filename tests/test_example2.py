"""응용 매뉴얼 Example 2 기준 결과 재현과 사용자 강우 기능 시험."""
import math
import os
import sys
import unittest

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from swmm_lid import InpFile, LidConfig, ScenarioConfig, load_model, run_scenario  # noqa: E402
from swmm_lid.scenario import gauge_interval_min  # noqa: E402
from swmm_lid.validation import load_reference, metric_from_report  # noqa: E402

EX2 = os.path.join(ROOT, "models", "Example2-Post.inp")
REF = os.path.join(ROOT, "validation", "manual_reference.csv")


@unittest.skipUnless(os.path.exists(EX2), "Example2-Post.inp 없음")
class Example2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = load_model(InpFile.from_file(EX2))

    def test_manual_reference_within_rounding(self):
        ref = load_reference(REF)
        ref = ref[ref["model"] == "Example2-Post.inp"]
        self.assertGreater(len(ref), 30)
        worst = 0.0
        for _, r in ref.iterrows():
            rt = r["routing"] if isinstance(r["routing"], str) else None
            res = run_scenario(self.model, ScenarioConfig(r["storm"], LidConfig(False), rt))
            v = metric_from_report(res.report, r["metric"])
            s = str(r["value"])
            nd = len(s.split(".")[1]) if "." in s else 0
            diff = abs(round(v, nd) - r["value"]) / r["value"] * 100
            worst = max(worst, diff)
            self.assertLess(diff, 1.5, f"{r['storm']} {r['metric']} {v} vs {r['value']}")
        print(f"\n  매뉴얼 기준값 {len(ref)}개, 최대 차이 {worst:.2f}%")

    def test_steady_100yr_floods_like_manual(self):
        res = run_scenario(self.model, ScenarioConfig("100-yr", routing="STEADY"))
        self.assertGreaterEqual(len(res.report.node_flooding), 1)   # 2.4절: 정상류 100년에서 월류

    def test_scaled_storm_equal_to_original(self):
        step = gauge_interval_min(self.model)
        vals = self.model.rain_at_interval("10-yr", step)
        a = run_scenario(self.model, ScenarioConfig("10-yr"))
        b = run_scenario(self.model, ScenarioConfig("user", custom_rain=vals))
        self.assertAlmostEqual(a.kpi["peak_flow_cms"], b.kpi["peak_flow_cms"], places=4)
        self.assertAlmostEqual(a.kpi["precip_mm"], b.kpi["precip_mm"], places=2)

    def test_long_uniform_storm_extends_simulation(self):
        step = gauge_interval_min(self.model)
        n = int(10 * 60 / step)                       # 10시간 균등 강우 (모의 기간 12시간 초과 필요)
        res = run_scenario(self.model, ScenarioConfig("u", custom_rain=[10.0] * n))
        self.assertAlmostEqual(res.kpi["precip_mm"], 100.0, delta=0.5)
        self.assertGreater(res.minutes[-1], 15.5 * 60)   # 원래 12시간 → 강우 10시간 + 여유 6시간
        self.assertLess(abs(res.kpi["runoff_error_pct"]), 1.0)


if __name__ == "__main__":
    unittest.main()
