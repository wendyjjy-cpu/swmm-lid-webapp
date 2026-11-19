"""SWMM 리포트 파일(.rpt)에서 요약표와 연속성 오차를 읽는다."""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class Report:
    text: str
    version: str = ""
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    runoff_continuity: dict[str, list[float]] = field(default_factory=dict)
    routing_continuity: dict[str, list[float]] = field(default_factory=dict)
    subcatch_runoff: list[dict] = field(default_factory=list)
    lid_performance: list[dict] = field(default_factory=list)
    node_depth: list[dict] = field(default_factory=list)
    node_inflow: list[dict] = field(default_factory=list)
    node_flooding: list[dict] = field(default_factory=list)
    outfall_loading: list[dict] = field(default_factory=list)
    link_flow: list[dict] = field(default_factory=list)

    @property
    def runoff_error_pct(self) -> float | None:
        v = self.runoff_continuity.get("Continuity Error (%)")
        return v[0] if v else None

    @property
    def routing_error_pct(self) -> float | None:
        v = self.routing_continuity.get("Continuity Error (%)")
        return v[0] if v else None


def _lines(text: str) -> list[str]:
    return text.splitlines()


def _find_title(lines: list[str], title: str, start: int = 0) -> int | None:
    for i in range(start, len(lines)):
        if lines[i].strip() == title:
            return i
    return None


def _table_rows(lines: list[str], title: str) -> list[list[str]]:
    """제목 아래 표의 데이터 행(토큰 목록). 표가 없으면(예: 'No nodes were flooded.') 빈 목록."""
    i = _find_title(lines, title)
    if i is None:
        return []
    dash = 0
    stars = 0
    rows = []
    j = i + 1
    # 제목 밑 '****' 줄, 빈 줄, 설명 문장 건너뛰기
    while j < len(lines):
        s = lines[j].strip()
        if s.startswith("-----"):
            dash += 1
            j += 1
            if dash == 2:
                break
            continue
        if dash == 0 and s.startswith("*"):
            stars += 1
            if stars > 1:          # 다음 섹션 제목에 도달 → 표 없음
                return []
        elif dash == 0 and s.lower().startswith(("no ", "none")):
            # 표 대신 문장(예: No nodes were flooded.)
            return []
        j += 1
    while j < len(lines):
        s = lines[j].strip()
        if not s:
            break
        if not s.startswith("-----"):
            rows.append(s.split())
        j += 1
    return rows


def _num(s: str) -> float:
    try:
        return float(s)
    except ValueError:
        return float("nan")


def _continuity(lines: list[str], title: str) -> dict[str, list[float]]:
    out = {}
    for i, line in enumerate(lines):
        if line.strip().startswith(title):
            j = i + 2
            while j < len(lines) and lines[j].strip():
                m = re.match(r"\s*(.+?)\s*\.{2,}\s*(.*)$", lines[j])
                if m:
                    key = m.group(1).strip()
                    vals = [_num(v) for v in m.group(2).split()]
                    out[key] = vals
                j += 1
            break
    return out


def parse_report(text: str) -> Report:
    lines = _lines(text)
    rep = Report(text=text)
    for line in lines[:10]:
        if "EPA STORM WATER MANAGEMENT MODEL" in line.upper() or "SWMM" in line:
            rep.version = line.strip()
            break
    for line in lines:
        s = line.strip()
        if s.startswith("ERROR"):
            rep.errors.append(s)
        elif s.startswith("WARNING"):
            rep.warnings.append(s)

    rep.runoff_continuity = _continuity(lines, "Runoff Quantity Continuity")
    rep.routing_continuity = _continuity(lines, "Flow Routing Continuity")

    for r in _table_rows(lines, "Subcatchment Runoff Summary"):
        if len(r) >= 11:
            rep.subcatch_runoff.append({
                "subcatchment": r[0], "precip": _num(r[1]), "runon": _num(r[2]),
                "evap": _num(r[3]), "infil": _num(r[4]), "imperv_runoff": _num(r[5]),
                "perv_runoff": _num(r[6]), "runoff_depth": _num(r[7]),
                "runoff_vol": _num(r[8]), "peak_runoff": _num(r[9]), "runoff_coeff": _num(r[10])})

    for r in _table_rows(lines, "LID Performance Summary"):
        if len(r) >= 10:
            rep.lid_performance.append({
                "subcatchment": r[0], "lid": r[1], "inflow": _num(r[2]), "evap": _num(r[3]),
                "infil": _num(r[4]), "surface_outflow": _num(r[5]), "drain_outflow": _num(r[6]),
                "initial_storage": _num(r[7]), "final_storage": _num(r[8]),
                "continuity_error": _num(r[9])})

    for r in _table_rows(lines, "Node Depth Summary"):
        if len(r) >= 7:
            rep.node_depth.append({
                "node": r[0], "type": r[1], "avg_depth": _num(r[2]), "max_depth": _num(r[3]),
                "max_hgl": _num(r[4]), "time_max": f"{r[5]} {r[6]}"})

    for r in _table_rows(lines, "Node Inflow Summary"):
        if len(r) >= 9:
            rep.node_inflow.append({
                "node": r[0], "type": r[1], "max_lateral": _num(r[2]), "max_total": _num(r[3]),
                "time_max": f"{r[4]} {r[5]}", "lateral_vol": _num(r[6]), "total_vol": _num(r[7]),
                "balance_error": _num(r[8])})

    for r in _table_rows(lines, "Node Flooding Summary"):
        if len(r) >= 7:
            rep.node_flooding.append({
                "node": r[0], "hours": _num(r[1]), "max_rate": _num(r[2]),
                "time_max": f"{r[3]} {r[4]}", "volume": _num(r[5]), "max_ponded": _num(r[6])})

    for r in _table_rows(lines, "Outfall Loading Summary"):
        if len(r) >= 5:
            rep.outfall_loading.append({
                "outfall": r[0], "freq": _num(r[1]), "avg_flow": _num(r[2]),
                "max_flow": _num(r[3]), "volume": _num(r[4])})

    for r in _table_rows(lines, "Link Flow Summary"):
        if len(r) >= 7:
            row = {"link": r[0], "type": r[1], "max_flow": _num(r[2]),
                   "time_max": f"{r[3]} {r[4]}", "max_veloc": _num(r[5]) if len(r) > 5 else float("nan"),
                   "max_full_flow": _num(r[6]) if len(r) > 6 else float("nan"),
                   "max_full_depth": _num(r[7]) if len(r) > 7 else float("nan")}
            rep.link_flow.append(row)
    return rep
