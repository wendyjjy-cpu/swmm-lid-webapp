"""Plotly 그래프."""
from __future__ import annotations

import math

import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from .model import Model

PALETTE = ["#2563eb", "#16a34a", "#dc2626", "#9333ea", "#ea580c", "#0891b2", "#4b5563", "#ca8a04"]
BASE_COLOR = "#6b7280"


def hydrograph(series: list[dict], rain: tuple[np.ndarray, np.ndarray] | None,
               xrange: tuple[float, float] | None = None, title: str = "") -> go.Figure:
    """series: [{label, minutes, values, dash?, color?}], rain: (minutes, mm/hr)."""
    if rain is None:
        fig = line_chart(series, "유량 (m³/s)", xrange, height=430)
        if title:
            fig.update_layout(title=title, margin=dict(t=40))
        return fig
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.28, 0.72],
                        vertical_spacing=0.04)
    if rain is not None and len(rain[0]):
        fig.add_trace(go.Bar(x=rain[0], y=rain[1], name="강우강도", marker_color="#60a5fa",
                             hovertemplate="%{x:.0f}분<br>%{y:.1f} mm/hr<extra></extra>"),
                      row=1, col=1)
    for i, s in enumerate(series):
        fig.add_trace(go.Scatter(
            x=s["minutes"], y=s["values"], name=s["label"], mode="lines",
            line=dict(color=s.get("color") or PALETTE[i % len(PALETTE)],
                      width=s.get("width", 2.2), dash=s.get("dash", "solid")),
            hovertemplate="%{x:.0f}분<br>%{y:.4f} m³/s<extra>" + s["label"] + "</extra>"),
            row=2, col=1)
    fig.update_yaxes(title_text="강우 (mm/hr)", autorange="reversed", row=1, col=1)
    fig.update_yaxes(title_text="유량 (m³/s)", rangemode="tozero", row=2, col=1)
    fig.update_xaxes(title_text="모의 시작 후 경과 시간 (분)", row=2, col=1)
    if xrange:
        fig.update_xaxes(range=list(xrange))
    fig.update_layout(height=470, margin=dict(l=10, r=10, t=40 if title else 10, b=10),
                      legend=dict(orientation="h", y=-0.18), title=title or None,
                      hovermode="x unified", bargap=0)
    return fig


def line_chart(series: list[dict], ytitle: str, xrange=None, height=330) -> go.Figure:
    fig = go.Figure()
    for i, s in enumerate(series):
        fig.add_trace(go.Scatter(x=s["minutes"], y=s["values"], name=s["label"], mode="lines",
                                 line=dict(color=s.get("color") or PALETTE[i % len(PALETTE)],
                                           width=2, dash=s.get("dash", "solid"))))
    fig.update_layout(height=height, margin=dict(l=10, r=10, t=10, b=10),
                      xaxis_title="경과 시간 (분)", yaxis_title=ytitle,
                      legend=dict(orientation="h", y=-0.25), hovermode="x unified")
    if xrange:
        fig.update_xaxes(range=list(xrange))
    return fig


def _centroid(pts):
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return sum(xs) / len(xs), sum(ys) / len(ys)


def _inside(x, y, pts) -> bool:
    """점이 다각형 안에 있는지 (ray casting)."""
    n, c = len(pts), False
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            xi = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < xi:
                c = not c
    return c


def _edge_dist(x, y, pts) -> float:
    best = float("inf")
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        dx, dy = x2 - x1, y2 - y1
        L2 = dx * dx + dy * dy
        t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / L2))
        px, py = x1 + t * dx, y1 + t * dy
        best = min(best, math.hypot(x - px, y - py))
    return best


def label_point(pts, grid: int = 30):
    """이름표 위치: 다각형 안쪽에서 경계로부터 가장 먼 점 (ㄴ자처럼 오목한 모양 대응)."""
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    best, best_d = _centroid(pts), -1.0
    for i in range(grid + 1):
        for j in range(grid + 1):
            x = x0 + (x1 - x0) * i / grid
            y = y0 + (y1 - y0) * j / grid
            if _inside(x, y, pts):
                d = _edge_dist(x, y, pts)
                if d > best_d:
                    best, best_d = (x, y), d
    return best


LID_COLORS = {  # 종류별 채우기·선 색
    "BC": ("rgba(22,163,74,0.40)", "#15803d"),
    "PP": ("rgba(37,99,235,0.35)", "#1d4ed8"),
    "IT": ("rgba(217,119,6,0.40)", "#b45309"),
}


def network_map(model: Model, lid_subs: dict[str, float] | None = None,
                link_values: dict[str, float] | None = None,
                link_label: str = "최대유량/만관유량",
                sub_values: dict[str, float] | None = None,
                sub_label: str = "",
                lid_types: dict[str, str] | None = None,
                type_labels: dict[str, str] | None = None) -> go.Figure:
    """소유역(다각형) + 관로 + 절점. lid_subs: {소유역: LID 면적(m²)}, lid_types: {소유역: LID 코드}."""
    lid_subs = lid_subs or {}
    lid_types = lid_types or {}
    type_labels = type_labels or {}
    fig = go.Figure()
    # 소유역
    for name, pts in model.polygons.items():
        if len(pts) < 3:
            continue
        xs = [p[0] for p in pts] + [pts[0][0]]
        ys = [p[1] for p in pts] + [pts[0][1]]
        has_lid = name in lid_subs
        sc = model.subcatchments.get(name)
        hover = f"<b>소유역 {name}</b>"
        if sc:
            hover += (f"<br>면적 {model.area_to_ha(sc.area):.2f} ha"
                      f"<br>불투수율 {sc.imperv:.1f}%<br>출구 {sc.outlet}")
        t = lid_types.get(name, "BC")
        if has_lid:
            hover += f"<br>LID {type_labels.get(t, t)} · 면적 {lid_subs[name]:.0f} m²"
        if sub_values and name in sub_values:
            hover += f"<br>{sub_label} {sub_values[name]:.2f}"
        fill, edge = LID_COLORS.get(t, LID_COLORS["BC"]) if has_lid else ("rgba(148,163,184,0.18)", "#94a3b8")
        fig.add_trace(go.Scatter(
            x=xs, y=ys, fill="toself", mode="lines", fillcolor=fill,
            line=dict(color=edge, width=1.2),
            hoverinfo="text", text=hover, hoveron="fills", showlegend=False))
        cx, cy = label_point(pts)
        fig.add_trace(go.Scatter(x=[cx], y=[cy], mode="text", text=[name],
                                 textfont=dict(size=11, color="#334155"),
                                 hoverinfo="skip", showlegend=False))
    # 관로
    vals = [v for v in (link_values or {}).values() if v is not None and not math.isnan(v)]
    vmax = max(vals + [1.0])
    for name, lk in model.links.items():
        if lk.from_node not in model.coords or lk.to_node not in model.coords:
            continue
        pts = [model.coords[lk.from_node]] + model.vertices.get(name, []) + [model.coords[lk.to_node]]
        v = (link_values or {}).get(name)
        color = _ramp(v / vmax) if v is not None and not math.isnan(v) else "#475569"
        hover = f"<b>{lk.kind.lower()} {name}</b><br>{lk.from_node} → {lk.to_node}"
        if v is not None and not math.isnan(v):
            hover += f"<br>{link_label}: {v:.2f}"
        fig.add_trace(go.Scatter(x=[p[0] for p in pts], y=[p[1] for p in pts], mode="lines",
                                 line=dict(color=color, width=4), hoverinfo="text",
                                 text=hover, showlegend=False))
    # 절점
    xs, ys, txt, sym, col = [], [], [], [], []
    for n, kind in model.node_types.items():
        if n not in model.coords:
            continue
        x, y = model.coords[n]
        xs.append(x); ys.append(y); txt.append(f"<b>{n}</b> ({kind.lower()})")
        sym.append("triangle-down" if kind == "OUTFALL" else "circle")
        col.append("#dc2626" if kind == "OUTFALL" else "#1e293b")
    fig.add_trace(go.Scatter(x=xs, y=ys, mode="markers", marker=dict(size=9, symbol=sym, color=col),
                             hoverinfo="text", text=txt, showlegend=False))
    # 범례용 더미
    used = list(dict.fromkeys(lid_types.get(s_, "BC") for s_ in lid_subs))
    for t in used:
        fig.add_trace(go.Scatter(x=[None], y=[None], mode="markers",
                                 marker=dict(size=12, color=LID_COLORS.get(t, LID_COLORS["BC"])[1],
                                             symbol="square"),
                                 name=type_labels.get(t, "LID 적용 소유역")))
    fig.add_trace(go.Scatter(x=[None], y=[None], mode="markers",
                             marker=dict(size=12, color="rgba(148,163,184,0.5)", symbol="square"),
                             name="LID 미적용 소유역"))
    if vals:
        fig.add_trace(go.Scatter(x=[None], y=[None], mode="markers", name=link_label,
                                 marker=dict(colorscale=[[0, _ramp(0)], [0.5, _ramp(0.5)], [1, _ramp(1)]],
                                             cmin=0, cmax=vmax, color=[0], showscale=True,
                                             colorbar=dict(title=dict(text=link_label, side="right"),
                                                           thickness=12, len=0.6))))
    fig.update_yaxes(scaleanchor="x", scaleratio=1, visible=False)
    fig.update_xaxes(visible=False)
    fig.update_layout(height=520, margin=dict(l=0, r=0, t=10, b=0),
                      legend=dict(orientation="h", y=-0.02), hovermode="closest")
    return fig


def _ramp(t: float) -> str:
    """0(파랑) → 0.5(노랑) → 1(빨강)."""
    t = max(0.0, min(1.0, t))
    if t < 0.5:
        a = t / 0.5
        c0, c1 = (37, 99, 235), (234, 179, 8)
    else:
        a = (t - 0.5) / 0.5
        c0, c1 = (234, 179, 8), (220, 38, 38)
    r, g, b = (int(c0[i] + (c1[i] - c0[i]) * a) for i in range(3))
    return f"rgb({r},{g},{b})"


def kpi_bars(labels: list[str], values: list[float], ytitle: str, colors=None) -> go.Figure:
    fig = go.Figure(go.Bar(x=labels, y=values,
                           marker_color=colors or [PALETTE[i % len(PALETTE)] for i in range(len(labels))],
                           text=[f"{v:.1f}" if v is not None and not math.isnan(v) else "-" for v in values],
                           textposition="outside"))
    fig.update_layout(height=330, margin=dict(l=10, r=10, t=10, b=10), yaxis_title=ytitle,
                      xaxis_tickangle=-15)
    return fig
