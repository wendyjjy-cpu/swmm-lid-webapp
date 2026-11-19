"""검증: (1) 매뉴얼 기준 결과와 비교 (2) EPA SWMM GUI 리포트와 비교 (3) 물수지 확인."""
from __future__ import annotations

import math
import os

import pandas as pd

from .report import Report, parse_report


def load_reference(path: str) -> pd.DataFrame | None:
    """기준 결과표(csv): model,storm,metric,value,unit,source."""
    if not os.path.exists(path):
        return None
    df = pd.read_csv(path, comment="#")
    need = {"model", "storm", "metric", "value", "unit", "source"}
    if not need.issubset(df.columns):
        return None
    return df


METRIC_LABELS = {
    "peak_outflow": "출구 첨두유량",
    "outflow_volume": "출구 유출량",
    "runoff_coeff": "유출계수",
    "runoff_depth": "유출고",
    "precip_depth": "강우량",
    "flood_nodes": "월류 절점 수",
}


_OBJ_METRICS = {
    "max_full_flow": ("link_flow", "link", "max_full_flow", "관로 {} 최대유량/만관유량"),
    "max_full_depth": ("link_flow", "link", "max_full_depth", "관로 {} 최대수심/만관수심"),
    "max_flow": ("link_flow", "link", "max_flow", "관로 {} 최대유량"),
    "node_max_depth": ("node_depth", "node", "max_depth", "절점 {} 최대수심"),
}


def metric_label(metric: str) -> str:
    if ":" in metric:
        kind, obj = metric.split(":", 1)
        if kind in _OBJ_METRICS:
            return _OBJ_METRICS[kind][3].format(obj)
    return METRIC_LABELS.get(metric, metric)


def metric_from_report(rep: Report, metric: str) -> float:
    if ":" in metric:
        kind, obj = metric.split(":", 1)
        if kind in _OBJ_METRICS:
            table, key, col, _ = _OBJ_METRICS[kind]
            row = next((r for r in getattr(rep, table) if r[key] == obj), None)
            return row[col] if row else math.nan
    if metric == "flood_nodes":
        return float(len(rep.node_flooding))
    sys_row = next((r for r in rep.outfall_loading if r["outfall"] == "System"), None)
    if sys_row is None and rep.outfall_loading:
        sys_row = rep.outfall_loading[0]
    rc = rep.runoff_continuity
    if metric == "peak_outflow":
        return sys_row["max_flow"] if sys_row else math.nan
    if metric == "outflow_volume":
        return sys_row["volume"] if sys_row else math.nan
    if metric == "runoff_depth":
        v = rc.get("Surface Runoff")
        return v[1] if v and len(v) > 1 else math.nan
    if metric == "precip_depth":
        v = rc.get("Total Precipitation")
        return v[1] if v and len(v) > 1 else math.nan
    if metric == "runoff_coeff":
        p, q = rc.get("Total Precipitation"), rc.get("Surface Runoff")
        if p and q and len(p) > 1 and p[1]:
            return q[1] / p[1]
        return math.nan
    return math.nan


def compare_reports(web: Report, gui: Report) -> pd.DataFrame:
    """웹앱 리포트와 GUI 리포트의 주요 값 비교."""
    rows = []

    def add(item, a, b, unit):
        diff = (a - b) / b * 100.0 if b and not math.isnan(a) and not math.isnan(b) else (
            0.0 if a == b else math.nan)
        rows.append({"항목": item, "웹앱": a, "SWMM GUI": b, "단위": unit, "차이(%)": diff})

    for m, unit in [("peak_outflow", "모형 유량 단위"), ("outflow_volume", "10^6 gal 또는 10^6 L"),
                    ("precip_depth", "in 또는 mm"), ("runoff_depth", "in 또는 mm")]:
        add(METRIC_LABELS[m], metric_from_report(web, m), metric_from_report(gui, m), unit)
    gl = {r["link"]: r for r in gui.link_flow}
    for r in web.link_flow:
        g = gl.get(r["link"])
        if g:
            add(f"관로 {r['link']} 최대유량", r["max_flow"], g["max_flow"], "모형 유량 단위")
    gn = {r["node"]: r for r in gui.node_depth}
    for r in web.node_depth:
        g = gn.get(r["node"])
        if g:
            add(f"절점 {r['node']} 최대수심", r["max_depth"], g["max_depth"], "ft 또는 m")
    gs = {r["subcatchment"]: r for r in gui.subcatch_runoff}
    for r in web.subcatch_runoff:
        g = gs.get(r["subcatchment"])
        if g:
            add(f"소유역 {r['subcatchment']} 첨두유출", r["peak_runoff"], g["peak_runoff"], "모형 유량 단위")
    return pd.DataFrame(rows)


# 응용 매뉴얼 표 4-13: 침투 트렌치 4곳의 유출계수(유출 부피 ÷ 유입 부피) 범위
EXAMPLE4_TRENCH_C = {"2-yr": (0.35, 0.59), "10-yr": (0.71, 0.79), "100-yr": (0.89, 0.92)}


def lid_runoff_coeff(rep: Report) -> float:
    """LID 유출계수 = (표면 월류 + 유공관 배수) ÷ 유입 (LID별 평균)."""
    vals = [(r["surface_outflow"] + r["drain_outflow"]) / r["inflow"]
            for r in rep.lid_performance if r["inflow"] > 0]
    return sum(vals) / len(vals) if vals else math.nan


def parse_uploaded_report(data: bytes) -> Report:
    return parse_report(data.decode("utf-8", errors="replace"))
