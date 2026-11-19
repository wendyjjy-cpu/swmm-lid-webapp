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


def network_map(model: Model, lid_subs: dict[str, float] | None = None,
                link_values: dict[str, float] | None = None,
                link_label: str = "최대유량/만관유량",
                sub_values: dict[str, float] | None = None,
                sub_label: str = "") -> go.Figure:
    """소유역(다각형) + 관로 + 절점. lid_subs: {소유역: LID 면적(m²)}."""
    lid_subs = lid_subs or {}
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
        if has_lid:
            hover += f"<br>LID 면적 {lid_subs[name]:.0f} m²"
        if sub_values and name in sub_values:
            hover += f"<br>{sub_label} {sub_values[name]:.2f}"
        fig.add_trace(go.Scatter(
            x=xs, y=ys, fill="toself", mode="lines",
            fillcolor="rgba(22,163,74,0.35)" if has_lid else "rgba(148,163,184,0.18)",
            line=dict(color="#15803d" if has_lid else "#94a3b8", width=1.2),
            hoverinfo="text", text=hover, hoveron="fills", showlegend=False))
        cx, cy = _centroid(pts)
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
    fig.add_trace(go.Scatter(x=[None], y=[None], mode="markers",
                             marker=dict(size=12, color="rgba(22,163,74,0.6)", symbol="square"),
                             name="LID 적용 소유역"))
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
