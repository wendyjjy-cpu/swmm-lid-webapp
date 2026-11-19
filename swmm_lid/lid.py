"""LID 설계값 템플릿과 시나리오별 INP 생성.

LID 매개변수는 SI(mm, mm/hr)로 정의하고, 모형이 US 단위면 in, in/hr로 바꿔 쓴다.
값은 EPA SWMM 5.2 사용자 매뉴얼의 LID 항목에 제시된 전형적인 범위 안에서 고른
기본값이며, 웹앱의 '고급 설정'에서 바꿀 수 있다.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field

from .inp import InpFile
from .model import Model

# ---------------------------------------------------------------- LID 종류
LID_TYPES = {
    "BC": {
        "label": "생태저류지 (Bio-retention cell)",
        "short": "생태저류지",
        "site": "perv",       # 투수면(잔디 등)에 설치 → 불투수면 그대로
        "sizing": 10.0,       # LID 면적 ÷ 처리 불투수면적 (%)
    },
    "PP": {
        "label": "투수성 포장 (Permeable pavement)",
        "short": "투수성 포장",
        "site": "imperv",     # 기존 불투수 포장을 대체
        "sizing": 30.0,
    },
    "IT": {
        "label": "침투 트렌치 (Infiltration trench)",
        "short": "침투 트렌치",
        "site": "perv",
        "sizing": 5.0,
    },
}

# 기본 설계값 (SI)
DEFAULT_PARAMS = {
    "BC": {
        "surface": {"berm_mm": 150.0, "veg_frac": 0.0, "n": 0.0, "slope": 0.0, "side": 5.0},
        "soil": {"thick_mm": 450.0, "porosity": 0.5, "fc": 0.2, "wp": 0.1,
                 "ksat_mmhr": 25.0, "kslope": 10.0, "suction_mm": 88.9},
        "storage": {"thick_mm": 300.0, "void_ratio": 0.67, "seep_mmhr": None},
    },
    "PP": {
        "surface": {"berm_mm": 0.0, "veg_frac": 0.0, "n": 0.0, "slope": 0.0, "side": 5.0},
        "pavement": {"thick_mm": 150.0, "void_ratio": 0.15, "imperv_frac": 0.0,
                     "perm_mmhr": 2540.0},
        "storage": {"thick_mm": 300.0, "void_ratio": 0.67, "seep_mmhr": None},
    },
    "IT": {
        "surface": {"berm_mm": 0.0, "veg_frac": 0.0, "n": 0.0, "slope": 0.0, "side": 5.0},
        "storage": {"thick_mm": 900.0, "void_ratio": 0.67, "seep_mmhr": None},
    },
}

# 막힘 단계 (투수·침투 능력 감소율, %) — 가정값
CLOG_LEVELS = {"없음 (신설)": 0.0, "보통": 75.0, "심함": 95.0}

FALLBACK_SEEPAGE_MMHR = 12.7   # 원지반 침투율을 모형에서 얻지 못할 때 (0.5 in/hr)


@dataclass
class LidConfig:
    enabled: bool = False
    lid_type: str = "BC"
    apply_pct: float = 50.0               # 처리하는 불투수면 비율 (%)
    sizing_pct: float | None = None       # LID 면적 ÷ 처리 불투수면적 (%), None이면 기본값
    targets: list[str] = field(default_factory=list)   # 적용 소유역
    clog_pct: float = 0.0                 # 막힘에 따른 투수능 감소율 (%)
    params: dict | None = None            # None이면 DEFAULT_PARAMS
    seepage_mmhr: float | None = None     # 원지반 침투율, None이면 모형에서 계산

    def resolved_params(self) -> dict:
        return copy.deepcopy(self.params or DEFAULT_PARAMS[self.lid_type])

    def resolved_sizing(self) -> float:
        return self.sizing_pct if self.sizing_pct is not None else LID_TYPES[self.lid_type]["sizing"]


def native_seepage_mmhr(model: Model) -> float:
    rate = model.native_infil_rate()
    if rate is None:
        return FALLBACK_SEEPAGE_MMHR
    return model.depth_to_mm(rate)


# ------------------------------------------------------------ INP 줄 만들기
def lid_control_rows(model: Model, cfg: LidConfig, name: str) -> list[list]:
    p = cfg.resolved_params()
    keep = 1.0 - cfg.clog_pct / 100.0          # 막힘 후 남는 투수능 비율
    d = model.mm_to_depth                       # mm → 모형 깊이 단위
    seep = cfg.seepage_mmhr if cfg.seepage_mmhr is not None else native_seepage_mmhr(model)
    t = cfg.lid_type
    rows: list[list] = [[name, t]]
    s = p["surface"]
    rows.append([name, "SURFACE", d(s["berm_mm"]), s["veg_frac"], s["n"], s["slope"], s["side"]])
    if t == "BC":
        so = p["soil"]
        rows.append([name, "SOIL", d(so["thick_mm"]), so["porosity"], so["fc"], so["wp"],
                     d(so["ksat_mmhr"] * keep), so["kslope"], d(so["suction_mm"])])
    if t == "PP":
        pv = p["pavement"]
        rows.append([name, "PAVEMENT", d(pv["thick_mm"]), pv["void_ratio"], pv["imperv_frac"],
                     d(pv["perm_mmhr"] * keep), 0, 0, 0])
    st = p["storage"]
    st_seep = st["seep_mmhr"] if st.get("seep_mmhr") is not None else seep
    rows.append([name, "STORAGE", d(st["thick_mm"]), st["void_ratio"], d(st_seep * keep), 0])
    rows.append([name, "DRAIN", 0, 0.5, 0, 6])   # 유공관 없음
    return rows


@dataclass
class LidPlacement:
    subcatchment: str
    lid_area_model: float      # 소유역 면적 단위(acre/ha)
    lid_area_unit: float       # LID_USAGE 면적 단위(ft²/m²)
    treated_imperv: float      # 처리 대상 불투수면적 (acre/ha)
    new_imperv_pct: float
    new_width: float
    clipped: bool


def plan_placements(model: Model, cfg: LidConfig) -> list[LidPlacement]:
    r = cfg.apply_pct / 100.0
    s = cfg.resolved_sizing() / 100.0
    site = LID_TYPES[cfg.lid_type]["site"]
    out = []
    for name in cfg.targets:
        sc = model.subcatchments.get(name)
        if sc is None or r <= 0:
            continue
        A = sc.area
        a_imp = A * sc.imperv / 100.0
        a_perv = A - a_imp
        L = s * r * a_imp
        limit = 0.9 * (a_perv if site == "perv" else a_imp)
        clipped = L > limit
        L = min(L, limit)
        if L <= 0:
            continue
        non_lid = A - L
        # SWMM은 %불투수와 폭을 'LID를 뺀 나머지 면적'에 적용한다(subcatch.c).
        if site == "perv":
            new_imp = a_imp / non_lid * 100.0
        else:
            new_imp = (a_imp - L) / non_lid * 100.0
        new_w = sc.width * non_lid / A        # 흐름길이(면적÷폭) 유지
        out.append(LidPlacement(name, L, model.area_to_lid_units(L), r * a_imp,
                                min(new_imp, 100.0), new_w, clipped))
    return out


def apply_lid(model: Model, inp: InpFile, cfg: LidConfig) -> list[LidPlacement]:
    """inp(사본)에 LID_CONTROLS·LID_USAGE를 쓰고 소유역 %불투수·폭을 보정한다."""
    if not cfg.enabled:
        return []
    placements = plan_placements(model, cfg)
    if not placements:
        return []
    name = f"LID_{cfg.lid_type}"
    inp.set_rows("LID_CONTROLS", lid_control_rows(model, cfg, name),
                 comment="Name Type/Layer Parameters (웹앱이 생성)",
                 after=["INFILTRATION", "SUBAREAS", "SUBCATCHMENTS"])
    usage = []
    for p in placements:
        usage.append([p.subcatchment, name, 1, round(p.lid_area_unit, 2), 0, 0,
                      round(cfg.apply_pct, 3), 0, "*", "*", 0])
    inp.set_rows("LID_USAGE", usage,
                 comment="Subcatch LID Number Area Width InitSat FromImp ToPerv RptFile DrainTo FromPerv",
                 after=["LID_CONTROLS"])
    by_name = {p.subcatchment: p for p in placements}
    new_rows = []
    for r in inp.rows("SUBCATCHMENTS"):
        p = by_name.get(r[0])
        if p is not None and len(r) >= 7:
            r = list(r)
            r[4] = round(p.new_imperv_pct, 4)
            r[5] = round(p.new_width, 4)
        new_rows.append(r)
    inp.set_rows("SUBCATCHMENTS", new_rows,
                 comment="Name RainGage Outlet Area %Imperv Width %Slope CurbLen SnowPack")
    return placements
