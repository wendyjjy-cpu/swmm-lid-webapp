"""LID 종류·배치·막힘 상태에 따른 개발지 유출 저감 효과 분석 웹앱 (EPA SWMM 5.2)."""
from __future__ import annotations

import copy
import io
import json
import math
import re
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

from swmm_lid import (CLOG_LEVELS, DEFAULT_PARAMS, LID_TYPES, InpFile, LidConfig, ScenarioConfig,
                      backend_name, compare_to_baseline, engine_version, lid_balance_table,
                      load_model, native_seepage_mmhr, run_scenario)
from swmm_lid import charts
from swmm_lid.runner import SwmmError
from swmm_lid.scenario import gauge_interval_min
from swmm_lid.validation import (EXAMPLE4_TRENCH_C, compare_reports, lid_runoff_coeff,
                                 load_reference, metric_from_report, metric_label,
                                 parse_uploaded_report)

APP_DIR = Path(__file__).parent
MODELS_DIR = APP_DIR / "models"
REF_CSV = APP_DIR / "validation" / "manual_reference.csv"
UPLOAD_KEY = "업로드한 INP 파일"

st.set_page_config(page_title="LID 유출 저감 시나리오 분석", page_icon="💧", layout="wide")


# =============================================================== 데이터 / 캐시
def model_files() -> dict[str, Path]:
    files = sorted(MODELS_DIR.glob("*.inp"))
    return {f.name: f for f in files}


@st.cache_data(show_spinner=False)
def read_text(path: str) -> str:
    return Path(path).read_text(encoding="utf-8", errors="replace")


@st.cache_resource(show_spinner=False, max_entries=10)
def get_model(inp_text: str):
    return load_model(InpFile(inp_text))


def cfg_to_json(cfg: ScenarioConfig) -> str:
    return json.dumps(asdict(cfg), sort_keys=True, ensure_ascii=False)


def cfg_from_json(s: str) -> ScenarioConfig:
    d = json.loads(s)
    return ScenarioConfig(storm=d["storm"], lid=LidConfig(**d["lid"]), routing=d.get("routing"),
                          custom_rain=d.get("custom_rain"))


@st.cache_resource(show_spinner=False, max_entries=300)
def run_cached(inp_text: str, cfg_json: str):
    model = get_model(inp_text)
    return run_scenario(model, cfg_from_json(cfg_json))


def baseline_json(cfg: ScenarioConfig) -> str:
    return cfg_to_json(ScenarioConfig(storm=cfg.storm, lid=LidConfig(enabled=False),
                                      routing=cfg.routing, custom_rain=cfg.custom_rain))


def fmt(v, nd=3, suffix=""):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "-"
    return f"{v:,.{nd}f}{suffix}"


# =============================================================== 엔진 확인
if backend_name() == "없음":
    st.error("SWMM 계산 엔진을 찾지 못했습니다. 터미널에서 `pip install -r requirements.txt`를 실행하세요.")
    st.stop()

# =============================================================== 사이드바: 모의조건
st.sidebar.title("① 모의조건 설정")
files = model_files()
choices = list(files) + [UPLOAD_KEY]
default_idx = next((i for i, n in enumerate(choices) if n.lower().startswith("example2")), 0)
model_name = st.sidebar.selectbox("모형 (INP 파일)", choices, index=default_idx,
                                  help="models 폴더의 INP 파일 또는 직접 올린 INP 파일")
if model_name == UPLOAD_KEY:
    up = st.sidebar.file_uploader("SWMM INP 파일 올리기", type=["inp"])
    if up is None:
        st.info("왼쪽에서 INP 파일을 올리면 모의를 시작할 수 있습니다.")
        st.stop()
    inp_text = up.getvalue().decode("utf-8", errors="replace")
else:
    inp_text = read_text(str(files[model_name]))
model = get_model(inp_text)

storms = model.rain_series_names()
if not storms:
    st.error("이 INP에는 강우 시계열(TIMESERIES)을 쓰는 강우계가 없습니다.")
    st.stop()


DEFAULT_STORM = next((i for i, s_ in enumerate(storms) if re.match(r"^10(\D|$)", s_)), 0)


def storm_label(s):
    d = model.rain_depth_mm(s)
    return f"{s} (총 {d:.1f} mm)" if d is not None else s


step_min = gauge_interval_min(model)
rain_mode = st.sidebar.radio(
    "강우 입력 방식", ["INP 설계강우", "총강우량 조정", "균등 강우", "직접 입력 (표)"],
    help="INP 설계강우: 모형에 들어 있는 강우 / 총강우량 조정: 시간분포는 유지하고 총량만 변경 / "
         "균등 강우: 총강우량과 지속시간 입력 / 직접 입력: 시간별 강우강도를 표에 입력")
custom_rain = None
if rain_mode == "INP 설계강우":
    storm = st.sidebar.selectbox("설계강우", storms, index=DEFAULT_STORM, format_func=storm_label)
elif rain_mode == "총강우량 조정":
    base_storm = st.sidebar.selectbox("시간분포를 가져올 설계강우", storms, index=DEFAULT_STORM,
                                      format_func=storm_label)
    base_vals = model.rain_at_interval(base_storm, step_min)
    base_depth = sum(base_vals) * step_min / 60.0
    total = st.sidebar.number_input("총강우량 (mm)", 1.0, 2000.0, float(round(base_depth, 1)), 5.0)
    custom_rain = [round(v * total / base_depth, 4) for v in base_vals] if base_depth > 0 else None
    storm = f"{base_storm}분포 {total:.0f}mm"
elif rain_mode == "균등 강우":
    total = st.sidebar.number_input("총강우량 (mm)", 1.0, 2000.0, 50.0, 5.0)
    dur = st.sidebar.number_input(f"지속시간 (분, {step_min:.0f}분 단위)", float(step_min), 1440.0,
                                  60.0, float(step_min))
    n_steps = max(1, int(round(dur / step_min)))
    custom_rain = [round(total / (n_steps * step_min / 60.0), 4)] * n_steps
    storm = f"균등 {total:.0f}mm/{n_steps * step_min:.0f}분"
else:
    base_storm = st.sidebar.selectbox("처음 값으로 쓸 설계강우", storms, index=DEFAULT_STORM,
                                      format_func=storm_label)
    df0 = pd.DataFrame({"강우강도 (mm/hr)": [round(v, 2) for v in model.rain_at_interval(base_storm, step_min)]})
    st.sidebar.caption(f"한 행 = {step_min:.0f}분. 값을 고치거나 행을 추가·삭제할 수 있고, "
                       "엑셀에서 한 열을 복사해 붙여 넣을 수도 있습니다.")
    edited = st.sidebar.data_editor(df0, num_rows="dynamic", hide_index=True,
                                    use_container_width=True, key=f"rain_editor_{base_storm}")
    vals = pd.to_numeric(edited["강우강도 (mm/hr)"], errors="coerce").fillna(0.0).clip(lower=0.0)
    custom_rain = [round(float(v), 4) for v in vals][:288] or [0.0]
    storm = f"직접입력 {sum(custom_rain) * step_min / 60:.0f}mm"
if custom_rain is not None:
    depth = sum(custom_rain) * step_min / 60.0
    st.sidebar.caption(f"입력 강우: 총 {depth:.1f} mm, 지속 {len(custom_rain) * step_min:.0f}분, "
                       f"최대 강도 {max(custom_rain):.1f} mm/hr")

st.sidebar.markdown("---")
lid_on = st.sidebar.toggle("LID 적용", value=True)
lid_type = st.sidebar.radio("LID 종류", list(LID_TYPES), format_func=lambda k: LID_TYPES[k]["label"],
                            disabled=not lid_on)
apply_pct = st.sidebar.slider("적용률: LID로 처리하는 불투수면 비율 (%)", 0, 100, 50, 5,
                              disabled=not lid_on)
up_grp, down_grp = model.upstream_downstream_groups()
place = st.sidebar.radio("배치 위치", ["전체 소유역", "상류 소유역", "하류 소유역", "직접 선택"],
                         disabled=not lid_on,
                         help="상류·하류는 각 소유역 출구에서 방류구까지 관로 길이로 나눕니다.")
all_subs = list(model.subcatchments)
if place == "전체 소유역":
    targets = all_subs
elif place == "상류 소유역":
    targets = up_grp
elif place == "하류 소유역":
    targets = down_grp
else:
    targets = st.sidebar.multiselect("LID를 넣을 소유역", all_subs, default=all_subs[:1],
                                     disabled=not lid_on)
if lid_on and place != "직접 선택":
    st.sidebar.caption("대상: " + ", ".join(targets))

clog_opts = list(CLOG_LEVELS) + ["직접 입력"]
clog_sel = st.sidebar.select_slider("막힘 정도", options=clog_opts, value=clog_opts[0],
                                    disabled=not lid_on,
                                    help="막힘을 투수·침투 능력 감소율로 표현합니다 (가정값).")
if clog_sel == "직접 입력":
    clog_pct = float(st.sidebar.slider("투수능 감소율 (%)", 0, 99, 50, disabled=not lid_on))
else:
    clog_pct = CLOG_LEVELS[clog_sel]
    st.sidebar.caption(f"투수·침투 능력 {clog_pct:.0f}% 감소")

native = native_seepage_mmhr(model)
with st.sidebar.expander("고급 설정"):
    sizing = st.number_input("LID 면적 비율 (처리 불투수면 대비, %)", 1.0, 100.0,
                             float(LID_TYPES[lid_type]["sizing"]), 1.0, disabled=not lid_on)
    seep = st.number_input("원지반 침투율 (mm/hr)", 0.0, 500.0, float(round(native, 2)), 0.5,
                           help="기본값은 모형의 침투 매개변수(Horton 종기침투능 또는 Green-Ampt "
                                "포화투수계수)의 면적가중 평균입니다.", disabled=not lid_on)
    params = copy.deepcopy(DEFAULT_PARAMS[lid_type])
    params["storage"]["thick_mm"] = st.number_input(
        "저류층(쇄석층) 두께 (mm)", 50.0, 3000.0, float(params["storage"]["thick_mm"]), 50.0,
        disabled=not lid_on)
    if lid_type == "BC":
        params["soil"]["ksat_mmhr"] = st.number_input(
            "토양층 투수계수 (mm/hr)", 1.0, 500.0, float(params["soil"]["ksat_mmhr"]), 1.0,
            disabled=not lid_on)
        params["surface"]["berm_mm"] = st.number_input(
            "표면 저류 깊이 (mm)", 0.0, 600.0, float(params["surface"]["berm_mm"]), 10.0,
            disabled=not lid_on)
    if lid_type == "PP":
        params["pavement"]["perm_mmhr"] = st.number_input(
            "포장층 투수계수 (mm/hr)", 10.0, 50000.0, float(params["pavement"]["perm_mmhr"]), 100.0,
            disabled=not lid_on)
    routing_opt = st.selectbox("흐름 추적 방법", ["INP 설정 그대로", "STEADY", "KINWAVE", "DYNWAVE"],
                               help="STEADY: 정상류, KINWAVE: 운동파, DYNWAVE: 동역학파")
routing = None if routing_opt == "INP 설정 그대로" else routing_opt

lid_cfg = LidConfig(enabled=bool(lid_on and targets and apply_pct > 0), lid_type=lid_type,
                    apply_pct=float(apply_pct), sizing_pct=float(sizing), targets=list(targets),
                    clog_pct=float(clog_pct), params=params, seepage_mmhr=float(seep))
cfg = ScenarioConfig(storm=storm, lid=lid_cfg, routing=routing, custom_rain=custom_rain)
cfg_json = cfg_to_json(cfg)

run_clicked = st.sidebar.button("② 모델 실행", type="primary", use_container_width=True)

ss = st.session_state
ss.setdefault("saved", [])        # 비교 목록: [{model, cfg, label}]
if run_clicked or ss.get("last_model") != model_name or "last_cfg" not in ss:
    ss.last_cfg = cfg_json
    ss.last_model = model_name
stale = ss.last_cfg != cfg_json

# =============================================================== 실행
try:
    with st.spinner("SWMM 모의 중…"):
        res = run_cached(inp_text, ss.last_cfg)
        base = run_cached(inp_text, baseline_json(res.config))
except (SwmmError, RuntimeError, ValueError) as e:
    st.error(f"모의 실패: {e}")
    st.stop()
cmp = compare_to_baseline(res, base)

# =============================================================== 머리말
st.title("LID 유출 저감 시나리오 분석")
st.caption(f"모형: **{model_name}** · 계산 엔진: EPA SWMM {engine_version()} ({backend_name()}) · "
           f"유량 단위: {model.flow_units} → 화면 표시는 SI 단위")
if stale:
    st.warning("왼쪽 설정이 바뀌었습니다. **② 모델 실행**을 누르면 결과가 갱신됩니다. "
               "(아래는 마지막으로 실행한 결과)")
st.markdown(f"**현재 결과:** {res.label}")

tab_dash, tab_cmp, tab_val, tab_model, tab_help = st.tabs(
    ["③ 결과 대시보드", "④ 시나리오 비교", "검증", "모형 정보", "사용 안내"])

# =============================================================== ③ 대시보드
with tab_dash:
    k, b = res.kpi, base.kpi
    on = bool(res.placements)
    site_ha = model.area_to_ha(model.total_area())
    c = st.columns(4)
    c[0].metric("첨두유량 (m³/s)", fmt(k["peak_flow_cms"], 3),
                delta=f"{-cmp['peak_reduction_pct']:+.1f}%" if on else None,
                delta_color="inverse", help="방류구 유입 첨두유량 (SWMM 리포트 값)")
    c[1].metric("총유출량 (m³)", fmt(k["outflow_vol_m3"], 0),
                delta=f"{-cmp['volume_reduction_pct']:+.1f}%" if on else None,
                delta_color="inverse")
    c[2].metric("첨두 발생 (분)", fmt(k["peak_time_min"], 0),
                delta=f"{cmp['peak_delay_min']:+.0f}분" if on else None,
                help="모의 시작 후 방류구 첨두가 나타난 시각")
    c[3].metric("유출계수", fmt(k["runoff_coeff"], 3),
                delta=f"{k['runoff_coeff'] - b['runoff_coeff']:+.3f}" if on else None,
                delta_color="inverse", help="지표 유출고 ÷ 강우량")
    c = st.columns(4)
    c[0].metric("최대 통수능 사용률", fmt(k["max_capacity"], 2),
                delta=f"{cmp['capacity_change']:+.2f}" if on else None,
                delta_color="inverse", help="관로 최대유량 ÷ 만관유량의 최대값 (1 이상이면 만관)")
    c[1].metric(f"최대 수심 (m) · {k['max_node_depth_node']}", fmt(k["max_node_depth_m"], 2),
                delta=f"{k['max_node_depth_m'] - b['max_node_depth_m']:+.2f} m" if on else None,
                delta_color="inverse", help="방류구를 뺀 절점 중 최대 수심이 가장 큰 곳")
    c[2].metric("월류 절점 수", f"{k['flood_nodes']}",
                delta=f"{k['flood_nodes'] - b['flood_nodes']:+d}" if on else None,
                delta_color="inverse", help=f"총 월류량 {fmt(k['flood_vol_m3'], 1)} m³")
    c[3].metric("LID 면적 (ha)", fmt(k["lid_area_ha"], 3),
                delta=f"유역의 {k['lid_area_ha'] / site_ha * 100:.1f}%" if on else None,
                delta_color="off")
    if on:
        st.caption("작은 글씨의 변화량은 같은 강우·같은 흐름 추적 방법의 **LID 없음** 모의 대비입니다.")
        if any(p.clipped for p in res.placements):
            st.info("일부 소유역은 설치 가능한 면적의 90%를 넘어 LID 면적을 줄여 넣었습니다.")

    # 수문곡선
    end_default = _end = float(res.minutes[-1]) if len(res.minutes) else 60.0
    if len(base.outflow_cms):
        thr = 0.01 * float(np.max(base.outflow_cms))
        idx = np.where(base.outflow_cms > thr)[0]
        if len(idx):
            end_default = min(_end, float(base.minutes[idx[-1]]) * 1.3 + 10)
    t_range = st.slider("그래프 시간 범위 (분)", 0.0, _end, (0.0, end_default), 1.0)
    series = [{"label": "LID 없음 (기준)", "minutes": base.minutes, "values": base.outflow_cms,
               "color": charts.BASE_COLOR, "dash": "dash"}]
    if res.placements:
        series.append({"label": res.label, "minutes": res.minutes, "values": res.outflow_cms,
                       "color": charts.PALETTE[1]})
    st.plotly_chart(charts.hydrograph(series, (res.minutes, res.rain_mmhr), t_range,
                                      title="방류구 유출 수문곡선"),
                    use_container_width=True)

    col_map, col_ts = st.columns([1.1, 1])
    with col_map:
        st.subheader("유역·관망 지도")
        link_vals = {r["link"]: r["max_full_flow"] for r in res.report.link_flow}
        lid_subs = {p.subcatchment: p.lid_area_unit * (0.092903 if model.us else 1.0)
                    for p in res.placements}
        if model.polygons or model.coords:
            st.plotly_chart(charts.network_map(model, lid_subs, link_vals), use_container_width=True)
        else:
            st.info("INP에 좌표가 없어 지도를 그릴 수 없습니다.")
    with col_ts:
        st.subheader("지점별 결과")
        kind = st.radio("대상", ["절점", "관로", "소유역"], horizontal=True)
        if kind == "절점":
            names, vars_ = res.out.nodes, {"수심 (m)": ("depth", 0.3048 if model.us else 1.0),
                                          "총유입량 (m³/s)": ("total_inflow", model.flow_to_cms(1.0)),
                                          "월류량 (m³/s)": ("flooding", model.flow_to_cms(1.0))}
            getter = lambda r, n, v: r.out.node_series(n, v)
        elif kind == "관로":
            names, vars_ = res.out.links, {"유량 (m³/s)": ("flow", model.flow_to_cms(1.0)),
                                          "수심 (m)": ("depth", 0.3048 if model.us else 1.0),
                                          "유속 (m/s)": ("velocity", 0.3048 if model.us else 1.0),
                                          "수심 비율 (수심/관경)": ("capacity", 1.0)}
            getter = lambda r, n, v: r.out.link_series(n, v)
        else:
            names, vars_ = res.out.subcatchments, {
                "유출량 (m³/s)": ("runoff", model.flow_to_cms(1.0)),
                "침투율 (mm/hr)": ("infil", 25.4 if model.us else 1.0),
                "강우강도 (mm/hr)": ("rainfall", 25.4 if model.us else 1.0)}
            getter = lambda r, n, v: r.out.subcatch_series(n, v)
        cc = st.columns(2)
        obj = cc[0].selectbox("이름", names)
        var_label = cc[1].selectbox("변수", list(vars_))
        var, factor = vars_[var_label]
        ser = [{"label": "LID 없음 (기준)", "minutes": base.minutes,
                "values": getter(base, obj, var) * factor, "color": charts.BASE_COLOR, "dash": "dash"}]
        if res.placements:
            ser.append({"label": "현재 시나리오", "minutes": res.minutes,
                        "values": getter(res, obj, var) * factor, "color": charts.PALETTE[1]})
        st.plotly_chart(charts.line_chart(ser, var_label, t_range), use_container_width=True)

    with st.expander("LID 물수지 (LID Performance Summary)", expanded=bool(res.placements)):
        if res.placements:
            st.dataframe(lid_balance_table(res).round(3), use_container_width=True, hide_index=True)
            st.caption("깊이 값은 LID 면적 기준 mm. 유입 = 증발 + 침투 + 표면 월류 + 유공관 배수 + 저류 변화.")
        else:
            st.write("LID가 적용되지 않았습니다.")
    with st.expander("소유역·관로·절점 요약표"):
        st.markdown("**소유역 유출 요약** (모형 단위)")
        st.dataframe(pd.DataFrame(res.report.subcatch_runoff), use_container_width=True, hide_index=True)
        st.markdown("**관로 최대유량** (모형 단위)")
        st.dataframe(pd.DataFrame(res.report.link_flow), use_container_width=True, hide_index=True)
        st.markdown("**절점 최대수심** (모형 단위)")
        st.dataframe(pd.DataFrame(res.report.node_depth), use_container_width=True, hide_index=True)

    d1, d2, d3 = st.columns(3)
    safe = res.label.replace(" ", "").replace("·", "_").replace("%", "pct")
    d1.download_button("시나리오 INP 내려받기", res.inp_text, file_name=f"{safe}.inp",
                       help="EPA SWMM 5.2 GUI에서 열어 같은 결과가 나오는지 확인할 수 있습니다.")
    d2.download_button("SWMM 리포트(.rpt) 내려받기", res.rpt_text, file_name=f"{safe}.rpt")
    kpi_df = pd.DataFrame([{"시나리오": res.label, **res.kpi, **cmp}])
    d3.download_button("KPI (CSV)", kpi_df.to_csv(index=False).encode("utf-8-sig"),
                       file_name=f"{safe}_kpi.csv")


# =============================================================== ④ 시나리오 비교
def variants(axis: str, cfg0: ScenarioConfig) -> list[ScenarioConfig]:
    out = []
    if axis == "LID 종류":
        for t in LID_TYPES:
            c = copy.deepcopy(cfg0)
            c.lid.enabled, c.lid.lid_type = True, t
            c.lid.sizing_pct, c.lid.params = None, None
            out.append(c)
    elif axis == "적용률":
        for p in (25, 50, 75, 100):
            c = copy.deepcopy(cfg0); c.lid.enabled = True; c.lid.apply_pct = float(p); out.append(c)
    elif axis == "막힘 정도":
        for v in CLOG_LEVELS.values():
            c = copy.deepcopy(cfg0); c.lid.enabled = True; c.lid.clog_pct = float(v); out.append(c)
    elif axis == "배치 위치":
        for grp in (all_subs, up_grp, down_grp):
            c = copy.deepcopy(cfg0); c.lid.enabled = True; c.lid.targets = list(grp); out.append(c)
    elif axis == "설계강우":
        for s in storms:
            c = copy.deepcopy(cfg0); c.storm = s; c.custom_rain = None; out.append(c)
    elif axis == "흐름 추적 방법":
        for rt_ in ("STEADY", "KINWAVE", "DYNWAVE"):
            c = copy.deepcopy(cfg0); c.routing = rt_; out.append(c)
    return out


def add_saved(cfg_s: str, label: str | None = None):
    for it in ss.saved:
        if it["cfg"] == cfg_s and it["model"] == model_name:
            return
    c = cfg_from_json(cfg_s)
    if label is None:
        label = c.label()
        if c.lid.enabled and set(c.lid.targets) == set(up_grp) and up_grp != all_subs:
            label += " (상류)"
        elif c.lid.enabled and set(c.lid.targets) == set(down_grp) and down_grp != all_subs:
            label += " (하류)"
    ss.saved.append({"model": model_name, "cfg": cfg_s, "label": label})


with tab_cmp:
    st.markdown("현재 결과를 비교 목록에 넣거나, 한 가지 조건만 바꿔 여러 시나리오를 한 번에 실행할 수 있습니다.")
    a1, a2, a3 = st.columns([1, 1.4, 1])
    if a1.button("현재 결과를 비교 목록에 추가", use_container_width=True):
        add_saved(ss.last_cfg)
    axis = a2.selectbox("일괄 실행: 바꿀 조건",
                        ["LID 종류", "적용률", "막힘 정도", "배치 위치", "설계강우", "흐름 추적 방법"],
                        label_visibility="collapsed")
    if a3.button(f"'{axis}' 일괄 실행", use_container_width=True):
        cur = cfg_from_json(ss.last_cfg)
        if not cur.lid.targets:
            cur.lid.targets = list(all_subs)
        prog = st.progress(0.0, text="일괄 실행 중…")
        vs = variants(axis, cur)
        for i, v in enumerate(vs):
            js = cfg_to_json(v)
            run_cached(inp_text, js)
            run_cached(inp_text, baseline_json(v))
            add_saved(js)
            prog.progress((i + 1) / len(vs), text=f"{i + 1}/{len(vs)} 완료")
        prog.empty()

    items = [it for it in ss.saved if it["model"] == model_name]
    if not items:
        st.info("비교 목록이 비어 있습니다.")
    else:
        rows, curves = [], []
        for i, it in enumerate(items):
            r = run_cached(inp_text, it["cfg"])
            bl = run_cached(inp_text, baseline_json(r.config))
            cm = compare_to_baseline(r, bl)
            lc = r.config.lid
            on = bool(r.placements)
            rows.append({
                "시나리오": it["label"], "설계강우": r.config.storm,
                "LID": LID_TYPES[lc.lid_type]["short"] if on else "없음",
                "적용률(%)": lc.apply_pct if on else 0, "소유역 수": len(r.placements),
                "막힘(%)": lc.clog_pct if on else 0,
                "첨두유량(m³/s)": r.kpi["peak_flow_cms"], "첨두 저감률(%)": cm["peak_reduction_pct"] if on else 0.0,
                "총유출량(m³)": r.kpi["outflow_vol_m3"], "유출량 저감률(%)": cm["volume_reduction_pct"] if on else 0.0,
                "첨두 지연(분)": cm["peak_delay_min"] if on else 0.0,
                "최대 통수능 사용률": r.kpi["max_capacity"], "월류 절점 수": r.kpi["flood_nodes"],
            })
            curves.append({"label": it["label"], "minutes": r.minutes, "values": r.outflow_cms})
        df = pd.DataFrame(rows)
        st.dataframe(df.round(3), use_container_width=True, hide_index=True)
        pick = st.multiselect("그래프에 표시할 시나리오", df["시나리오"].tolist(),
                              default=df["시나리오"].tolist()[:6])
        sel = [c_ for c_ in curves if c_["label"] in pick]
        if sel:
            st.plotly_chart(charts.hydrograph(sel, None, None, title="수문곡선 비교"),
                            use_container_width=True)
            sub = df[df["시나리오"].isin(pick)]
            g1, g2 = st.columns(2)
            g1.plotly_chart(charts.kpi_bars(sub["시나리오"].tolist(), sub["첨두 저감률(%)"].tolist(),
                                            "첨두 저감률 (%)"), use_container_width=True)
            g2.plotly_chart(charts.kpi_bars(sub["시나리오"].tolist(), sub["유출량 저감률(%)"].tolist(),
                                            "유출량 저감률 (%)"), use_container_width=True)
        e1, e2 = st.columns(2)
        e1.download_button("비교표 (CSV)", df.to_csv(index=False).encode("utf-8-sig"),
                           file_name="scenario_comparison.csv")
        if e2.button("비교 목록 비우기"):
            ss.saved = [it for it in ss.saved if it["model"] != model_name]
            st.rerun()

# =============================================================== 검증
with tab_val:
    st.subheader("1. 기준 모형 검증: 공식 결과와 비교")
    ref = load_reference(str(REF_CSV))
    ref_m = ref[ref["model"] == model_name] if ref is not None else None
    if ref_m is None or ref_m.empty:
        st.info("이 모형의 기준 결과표가 아직 없습니다 (validation/manual_reference.csv).")
    else:
        vrows = []
        for _, rr in ref_m.iterrows():
            if rr["storm"] not in storms:
                continue
            rt = rr.get("routing") if "routing" in ref_m.columns else None
            rt = rt if isinstance(rt, str) and rt.strip() else None
            b0 = run_cached(inp_text, cfg_to_json(ScenarioConfig(rr["storm"], LidConfig(False), rt)))
            web_v = metric_from_report(b0.report, rr["metric"])
            ref_v = float(rr["value"])
            # 기준값의 반올림 자릿수에 맞춰 웹앱 값도 반올림해 비교
            s_val = str(rr["value"])
            nd = len(s_val.split(".")[1]) if "." in s_val else 0
            web_r = round(web_v, nd) if not math.isnan(web_v) else web_v
            diff = (web_r - ref_v) / ref_v * 100 if ref_v else math.nan
            vrows.append({"출처": rr["source"], "설계강우": rr["storm"], "흐름 추적": rt or "INP 설정",
                          "항목": metric_label(rr["metric"]), "기준값": ref_v, "웹앱": web_v,
                          "웹앱(반올림)": web_r, "단위": rr["unit"], "차이(%)": diff})
        vdf = pd.DataFrame(vrows)
        if not vdf.empty:
            ad = vdf["차이(%)"].abs()
            v1, v2, v3, v4 = st.columns(4)
            v1.metric("비교 항목 수", f"{len(vdf)}")
            v2.metric("차이 1% 이내", f"{int((ad <= 1.0).sum())}개")
            v3.metric("차이 5% 이내", f"{int((ad <= 5.0).sum())}개")
            v4.metric("최대 차이", f"{ad.max():.2f}%")
        st.dataframe(vdf.round(4), use_container_width=True, hide_index=True)
        st.caption("기준값은 SWMM 5.0 시절 매뉴얼 표의 값(소수 둘째 자리 등으로 반올림)입니다. "
                   "웹앱 값을 같은 자릿수로 반올림해 비교하며, 남는 차이는 반올림·엔진 버전 차이입니다.")

    st.subheader("2. EPA SWMM GUI 결과와 비교")
    st.markdown("① 대시보드에서 **시나리오 INP 내려받기** → ② EPA SWMM 5.2에서 열어 실행 → "
                "③ 리포트를 저장(File ▸ Save As, .rpt)해 아래에 올리면 현재 결과와 비교합니다.")
    gui = st.file_uploader("SWMM GUI 리포트 (.rpt / .txt)", type=["rpt", "txt"])
    if gui is not None:
        cdf = compare_reports(res.report, parse_uploaded_report(gui.getvalue()))
        st.dataframe(cdf.round(4), use_container_width=True, hide_index=True)
        if not cdf.empty:
            st.caption(f"최대 절대 차이 {cdf['차이(%)'].abs().max():.3f}% (리포트 소수점 자리 때문에 생기는 "
                       "차이는 반올림 오차입니다).")

    st.subheader("3. 물수지(연속성) 확인")
    w1, w2, w3 = st.columns(3)
    w1.metric("유출 연속성 오차 (%)", fmt(res.kpi["runoff_error_pct"], 3))
    w2.metric("흐름 추적 연속성 오차 (%)", fmt(res.kpi["routing_error_pct"], 3))
    lid_err = max((abs(r["continuity_error"]) for r in res.report.lid_performance), default=float("nan"))
    w3.metric("LID 물수지 최대 오차 (%)", fmt(lid_err, 3))
    st.caption("SWMM은 매 모의마다 물수지를 계산합니다. 오차가 ±1% 안팎이면 계산이 안정적이라고 봅니다.")

    st.subheader("4. LID 계산 경향 확인: 매뉴얼 Example 4와 비교")
    if not set(EXAMPLE4_TRENCH_C).issubset(storms) or not {"S1", "S2"}.issubset(model.subcatchments):
        st.info("응용 매뉴얼 Example 2 유역에서만 표시됩니다.")
    else:
        st.markdown("Example 4는 소유역 S1·S2에 침투 트렌치를 넣고, 트렌치 유출계수(유출 부피 ÷ 유입 부피)를 "
                    "보고했습니다(표 4-13). 트렌치를 투수 소유역으로 표현한 옛 방식이라 값이 같을 수는 없고, "
                    "**강우가 커질수록 효과가 줄어드는 경향**이 같은지 확인합니다. 웹앱 쪽은 S1·S2에 침투 트렌치 "
                    "(적용률 50%, 기본 설계값)를 넣은 결과입니다.")
        trows = []
        for s_, (lo, hi) in EXAMPLE4_TRENCH_C.items():
            rr_ = run_cached(inp_text, cfg_to_json(ScenarioConfig(
                s_, LidConfig(True, "IT", 50.0, None, ["S1", "S2"]))))
            cw = lid_runoff_coeff(rr_.report)
            trows.append({"설계강우": s_, "매뉴얼 트렌치 유출계수 (표 4-13)": f"{lo:.2f} ~ {hi:.2f}",
                          "웹앱 트렌치 유출계수": round(cw, 2),
                          "웹앱 부피 저감률(%)": round((1 - cw) * 100, 1)})
        tdf = pd.DataFrame(trows)
        st.dataframe(tdf, use_container_width=True, hide_index=True)
        mono = all(tdf["웹앱 트렌치 유출계수"].diff().dropna() > 0)
        st.caption("웹앱 결과도 강우가 커질수록 유출계수가 커져(효과 감소) 매뉴얼과 같은 경향입니다."
                   if mono else "웹앱 결과의 경향이 매뉴얼과 다릅니다. 설계값을 확인하세요.")

    if res.report.warnings:
        with st.expander(f"SWMM 경고 {len(res.report.warnings)}건"):
            for w in res.report.warnings:
                st.write(w)

# =============================================================== 모형 정보
with tab_model:
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("유역 면적 (ha)", fmt(model.area_to_ha(model.total_area()), 2))
    m2.metric("면적가중 불투수율 (%)", fmt(model.mean_imperv(), 1))
    m3.metric("소유역 / 절점 / 관로", f"{len(model.subcatchments)} / {len(model.node_types)} / {len(model.links)}")
    m4.metric("원지반 침투율 (mm/hr)", fmt(native, 2))
    st.markdown(f"흐름 추적: **{model.inp.get_option('FLOW_ROUTING', '-')}** · 침투: "
                f"**{model.infil_method}** · 상류 그룹: {', '.join(up_grp)} · 하류 그룹: {', '.join(down_grp)}")
    st.markdown("**설계강우**")
    st.dataframe(pd.DataFrame([{"이름": s, "총강우량(mm)": model.rain_depth_mm(s)} for s in storms]).round(2),
                 hide_index=True)
    hy = model.rain_hyetograph(storm)
    if hy:
        st.plotly_chart(charts.line_chart([{"label": storm, "minutes": [h[0] for h in hy],
                                            "values": [h[1] for h in hy]}], "강우강도 (mm/hr)"),
                        use_container_width=True)
    st.markdown("**소유역**")
    dl = model.downstream_length()
    st.dataframe(pd.DataFrame([{
        "소유역": s.name, "출구": s.outlet, "면적(ha)": model.area_to_ha(s.area), "불투수율(%)": s.imperv,
        "폭": s.width, "경사(%)": s.slope, "방류구까지 관로 길이": dl.get(s.name)}
        for s in model.subcatchments.values()]).round(3), use_container_width=True, hide_index=True)
    st.markdown(f"**LID 설계값: {LID_TYPES[lid_type]['label']}** (SI 단위, 막힘 적용 전)")
    prm = []
    for layer, vals in params.items():
        for kk, vv in vals.items():
            prm.append({"층": layer, "항목": kk, "값": "원지반 침투율 사용" if vv is None else vv})
    st.dataframe(pd.DataFrame(prm), hide_index=True)

# =============================================================== 사용 안내
with tab_help:
    st.markdown((APP_DIR / "docs" / "guide.md").read_text(encoding="utf-8")
                if (APP_DIR / "docs" / "guide.md").exists() else "안내 문서가 없습니다.")
