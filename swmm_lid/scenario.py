"""시나리오(강우 + LID 조건)로 INP를 만들고 실행해 KPI를 계산한다."""
from __future__ import annotations

import hashlib
import math
import os
import tempfile
from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

from .inp import InpFile
from .lid import LID_TYPES, LidConfig, LidPlacement, apply_lid
from .model import Model, load_model
from .outfile import OutFile, read_out
from .report import Report, parse_report
from .runner import run_swmm

GAL6_TO_M3 = 3785.41      # 10^6 gal → m³
LTR6_TO_M3 = 1000.0       # 10^6 ltr → m³


USER_STORM = "USER_STORM"


@dataclass
class ScenarioConfig:
    storm: str                          # INP 강우 시계열 이름 (사용자 강우면 표시용 이름)
    lid: LidConfig = field(default_factory=LidConfig)
    routing: str | None = None          # None이면 INP 설정 그대로
    custom_rain: list[float] | None = None   # 사용자 강우: 강우계 간격별 강우강도 (mm/hr)

    def key(self, base_text: str) -> str:
        h = hashlib.sha1(base_text.encode())
        h.update(repr(asdict(self)).encode())
        return h.hexdigest()[:16]

    def storm_label(self) -> str:
        return self.storm

    def label(self) -> str:
        rt = f" · {self.routing}" if self.routing else ""
        if not self.lid.active():
            return f"{self.storm_label()} · LID 없음{rt}"
        items = self.lid.items()
        if self.lid.is_mixed:
            kinds = "·".join(LID_TYPES[t]["short"] for t in self.lid.used_types())
            rates = [r_ for _, _, r_ in items]
            rate_s = (f"{rates[0]:.0f}%" if max(rates) - min(rates) < 1e-9
                      else f"{min(rates):.0f}~{max(rates):.0f}%")
            return (f"{self.storm_label()} · 혼합({kinds}) {rate_s} · 소유역 {len(items)}곳 · "
                    f"막힘 {self.lid.clog_pct:.0f}%{rt}")
        lt = LID_TYPES[self.lid.lid_type]["short"]
        return (f"{self.storm_label()} · {lt} {self.lid.apply_pct:.0f}% · "
                f"소유역 {len(items)}곳 · 막힘 {self.lid.clog_pct:.0f}%{rt}")


def _hours(s: str) -> float:
    parts = [float(p) for p in str(s).split(":")]
    while len(parts) < 3:
        parts.append(0.0)
    return parts[0] + parts[1] / 60 + parts[2] / 3600


def _ensure_sim_hours(inp: InpFile, hours_needed: float) -> None:
    """모의 종료 시각이 시작 후 hours_needed 시간보다 이르면 늦춘다."""
    from datetime import datetime, timedelta
    sd = inp.get_option("START_DATE")
    if not sd:
        return
    st_ = inp.get_option("START_TIME", "00:00:00") or "00:00:00"
    ed = inp.get_option("END_DATE", sd) or sd
    et = inp.get_option("END_TIME", "00:00:00") or "00:00:00"
    try:
        start = datetime.strptime(sd, "%m/%d/%Y") + timedelta(hours=_hours(st_))
        end = datetime.strptime(ed, "%m/%d/%Y") + timedelta(hours=_hours(et))
    except ValueError:
        return
    need = start + timedelta(hours=hours_needed)
    if need > end:
        inp.set_option("END_DATE", need.strftime("%m/%d/%Y"))
        inp.set_option("END_TIME", need.strftime("%H:%M:%S"))


def gauge_interval_min(model: Model) -> float:
    g = next(iter(model.raingages.values()), None)
    return _hours(g[1]) * 60.0 if g else 5.0


@dataclass
class ScenarioResult:
    config: ScenarioConfig
    label: str
    inp_text: str
    rpt_text: str
    report: Report
    placements: list[LidPlacement]
    minutes: np.ndarray
    outflow_cms: np.ndarray          # 계통 방류량 (m³/s)
    rain_mmhr: np.ndarray            # 유역 평균 강우강도 (mm/hr)
    kpi: dict
    out: OutFile
    model: Model

    def node_series(self, node: str, var: str) -> np.ndarray:
        return self.out.node_series(node, var)

    def link_series(self, link: str, var: str) -> np.ndarray:
        return self.out.link_series(link, var)


def build_inp(model: Model, cfg: ScenarioConfig) -> tuple[InpFile, list[LidPlacement]]:
    inp = model.inp.copy()
    # 1) 설계강우: 강우계가 참조하는 시계열 이름 교체 (사용자 강우면 새 시계열 추가)
    series = cfg.storm
    if cfg.custom_rain:
        step = gauge_interval_min(model)
        ts_rows = []
        for k, mmhr in enumerate(list(cfg.custom_rain) + [0.0]):
            t = k * step
            ts_rows.append([USER_STORM, f"{int(t // 60)}:{int(t % 60):02d}",
                            round(model.mm_to_depth(max(0.0, float(mmhr))), 5)])
        inp.append_rows("TIMESERIES", ts_rows)
        series = USER_STORM
        _ensure_sim_hours(inp, len(cfg.custom_rain) * step / 60.0 + 6.0)
    rows = []
    for r in inp.rows("RAINGAGES"):
        r = list(r)
        if len(r) >= 6 and r[4].upper() == "TIMESERIES":
            r[5] = series
            if cfg.custom_rain:
                r[1] = "INTENSITY"
        rows.append(r)
    if rows:
        inp.set_rows("RAINGAGES", rows, comment="Name Format Interval SCF Source")
    # 2) 라우팅 방법
    if cfg.routing:
        inp.set_option("FLOW_ROUTING", cfg.routing)
    # 3) 모든 객체 결과를 이진 파일에 저장
    inp.set_rows("REPORT", [["INPUT", "NO"], ["CONTROLS", "NO"], ["SUBCATCHMENTS", "ALL"],
                            ["NODES", "ALL"], ["LINKS", "ALL"]])
    # 4) LID
    placements = apply_lid(model, inp, cfg.lid)
    return inp, placements


def run_scenario(model: Model, cfg: ScenarioConfig, workdir: str | None = None) -> ScenarioResult:
    inp, placements = build_inp(model, cfg)
    text = inp.to_text()
    tmp = workdir or tempfile.mkdtemp(prefix="swmm_")
    os.makedirs(tmp, exist_ok=True)
    key = cfg.key(model.inp.to_text())
    ip, rp, op = (os.path.join(tmp, f"{key}.{e}") for e in ("inp", "rpt", "out"))
    with open(ip, "w", encoding="utf-8") as f:
        f.write(text)
    run_swmm(ip, rp, op)
    with open(rp, "r", encoding="utf-8", errors="replace") as f:
        rpt_text = f.read()
    report = parse_report(rpt_text)
    if report.errors:
        raise RuntimeError("SWMM 오류: " + " / ".join(report.errors[:5]))
    out = read_out(op)
    smodel = load_model(inp)          # LID 반영된 모형(면적 보정 포함)
    minutes = out.minutes()
    outflow = np.array([model.flow_to_cms(v) for v in out.system_series("outflow")])
    rain = np.array([model.depth_to_mm(v) for v in out.system_series("rainfall")])
    kpi = compute_kpi(model, report, minutes, outflow, placements)
    for p in (ip, rp, op):
        try:
            os.remove(p)
        except OSError:
            pass
    return ScenarioResult(cfg, cfg.label(), text, rpt_text, report, placements,
                          minutes, outflow, rain, kpi, out, smodel)


def _vol_to_m3(model: Model, v: float) -> float:
    return v * (GAL6_TO_M3 if model.us else LTR6_TO_M3)


def compute_kpi(model: Model, rep: Report, minutes: np.ndarray, outflow: np.ndarray,
                placements: list[LidPlacement]) -> dict:
    sys_row = next((r for r in rep.outfall_loading if r["outfall"] == "System"), None)
    if sys_row is None and rep.outfall_loading:
        sys_row = rep.outfall_loading[0]
    peak_model_units = sys_row["max_flow"] if sys_row else float(np.max(outflow)) / model.flow_to_cms(1)
    vol_model_units = sys_row["volume"] if sys_row else float("nan")
    i_peak = int(np.argmax(outflow)) if len(outflow) else 0
    rc = rep.runoff_continuity
    precip = rc.get("Total Precipitation", [math.nan, math.nan])
    infil = rc.get("Infiltration Loss", [math.nan, math.nan])
    runoff = rc.get("Surface Runoff", [math.nan, math.nan])
    flood_vol = sum(r["volume"] for r in rep.node_flooding)
    conduits = [r for r in rep.link_flow if r["type"].upper() == "CONDUIT"
                and not math.isnan(r["max_full_flow"])]
    max_cap = max((r["max_full_flow"] for r in conduits), default=math.nan)
    n_full = sum(1 for r in conduits if r["max_full_flow"] >= 1.0)
    juncs = [r for r in rep.node_depth if r["type"].upper() != "OUTFALL"
             and not math.isnan(r["max_depth"])]
    deep = max(juncs, key=lambda r: r["max_depth"], default=None)
    to_m = 0.3048 if model.us else 1.0
    lid_area_ha = sum(model.area_to_ha(p.lid_area_model) for p in placements)
    return {
        "peak_flow_cms": model.flow_to_cms(peak_model_units),
        "peak_flow_model": peak_model_units,
        "peak_time_min": float(minutes[i_peak]) if len(minutes) else math.nan,
        "outflow_vol_m3": _vol_to_m3(model, vol_model_units),
        "outflow_vol_model": vol_model_units,
        "precip_mm": model.depth_to_mm(precip[1]) if len(precip) > 1 else math.nan,
        "infil_mm": model.depth_to_mm(infil[1]) if len(infil) > 1 else math.nan,
        "runoff_mm": model.depth_to_mm(runoff[1]) if len(runoff) > 1 else math.nan,
        "runoff_coeff": (runoff[1] / precip[1]) if len(precip) > 1 and precip[1] else math.nan,
        "flood_nodes": len(rep.node_flooding),
        "flood_vol_m3": _vol_to_m3(model, flood_vol),
        "max_capacity": max_cap,
        "n_full_conduits": n_full,
        "max_node_depth_m": deep["max_depth"] * to_m if deep else math.nan,
        "max_node_depth_node": deep["node"] if deep else "-",
        "lid_area_ha": lid_area_ha,
        "runoff_error_pct": rep.runoff_error_pct,
        "routing_error_pct": rep.routing_error_pct,
    }


def compare_to_baseline(res: ScenarioResult, base: ScenarioResult) -> dict:
    k, b = res.kpi, base.kpi

    def red(x, y):
        return (1 - x / y) * 100.0 if y and not math.isnan(y) and not math.isnan(x) else math.nan

    ha = k.get("lid_area_ha") or 0.0
    return {
        "peak_reduction_pct": red(k["peak_flow_cms"], b["peak_flow_cms"]),
        "volume_reduction_pct": red(k["outflow_vol_m3"], b["outflow_vol_m3"]),
        "peak_delay_min": k["peak_time_min"] - b["peak_time_min"],
        "capacity_change": k["max_capacity"] - b["max_capacity"],
        # LID 면적 1 ha당 저감량 (배치·종류를 같은 LID 면적 기준으로 비교)
        "vol_red_m3_per_ha": (b["outflow_vol_m3"] - k["outflow_vol_m3"]) / ha if ha > 0 else math.nan,
        "peak_red_lps_per_ha": (b["peak_flow_cms"] - k["peak_flow_cms"]) * 1000 / ha if ha > 0 else math.nan,
    }


def lid_balance_table(res: ScenarioResult) -> pd.DataFrame:
    """LID 물수지(LID Performance Summary) – 깊이는 mm, 부피는 m³로 환산."""
    m = res.model
    area = {p.subcatchment: p.lid_area_unit for p in res.placements}
    to_m2 = 0.092903 if m.us else 1.0
    rows = []
    for r in res.report.lid_performance:
        a_m2 = area.get(r["subcatchment"], math.nan) * to_m2
        d = {k: m.depth_to_mm(r[k]) for k in ("inflow", "evap", "infil", "surface_outflow",
                                              "drain_outflow", "initial_storage", "final_storage")}
        rows.append({
            "소유역": r["subcatchment"],
            "LID": LID_TYPES.get(r["lid"].replace("LID_", ""), {}).get("short", r["lid"]),
            "LID 면적(m²)": a_m2,
            "유입(mm)": d["inflow"], "증발(mm)": d["evap"], "침투(mm)": d["infil"],
            "표면 월류(mm)": d["surface_outflow"], "유공관 배수(mm)": d["drain_outflow"],
            "최종 저류(mm)": d["final_storage"], "유입 부피(m³)": d["inflow"] / 1000 * a_m2,
            "침투 부피(m³)": d["infil"] / 1000 * a_m2,
            "연속 오차(%)": r["continuity_error"],
        })
    return pd.DataFrame(rows)
