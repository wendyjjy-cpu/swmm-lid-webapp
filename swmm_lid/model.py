"""INP에서 웹앱에 필요한 모형 정보(단위, 소유역, 관망, 좌표, 강우)를 추출."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from .inp import InpFile

US_FLOW_UNITS = {"CFS", "GPM", "MGD"}
# 유량 단위 → m3/s
FLOW_TO_CMS = {"CFS": 0.0283168, "GPM": 6.30902e-5, "MGD": 0.0438126,
               "CMS": 1.0, "LPS": 0.001, "MLD": 0.0115741}


@dataclass
class Subcatchment:
    name: str
    gage: str
    outlet: str
    area: float          # acres(US) 또는 ha(SI)
    imperv: float        # %
    width: float         # ft 또는 m
    slope: float         # %
    rest: list[str] = field(default_factory=list)   # CurbLen, SnowPack 등


@dataclass
class Link:
    name: str
    kind: str            # CONDUIT, PUMP, ORIFICE, WEIR, OUTLET
    from_node: str
    to_node: str
    length: float = 0.0


@dataclass
class Model:
    inp: InpFile
    flow_units: str
    us: bool
    subcatchments: dict[str, Subcatchment]
    node_types: dict[str, str]
    links: dict[str, Link]
    coords: dict[str, tuple[float, float]]
    vertices: dict[str, list[tuple[float, float]]]
    polygons: dict[str, list[tuple[float, float]]]
    raingages: dict[str, list[str]]
    timeseries: dict[str, list[tuple[str, float]]]
    infil_method: str
    infiltration: dict[str, list[str]]

    # ------------------------------------------------------------ 단위 도우미
    @property
    def area_label(self) -> str:
        return "acre" if self.us else "ha"

    @property
    def depth_label(self) -> str:
        return "in" if self.us else "mm"

    def area_to_ha(self, a: float) -> float:
        return a * 0.404686 if self.us else a

    def area_to_lid_units(self, a: float) -> float:
        """소유역 면적 단위 → LID_USAGE 면적 단위(ft² 또는 m²)."""
        return a * 43560.0 if self.us else a * 10000.0

    def depth_to_mm(self, d: float) -> float:
        return d * 25.4 if self.us else d

    def mm_to_depth(self, mm: float) -> float:
        return mm / 25.4 if self.us else mm

    def flow_to_cms(self, q: float) -> float:
        return q * FLOW_TO_CMS.get(self.flow_units, 1.0)

    # --------------------------------------------------------------- 강우
    def rain_series_names(self) -> list[str]:
        """강우계가 쓰는 시계열을 앞에 두고, 나머지 시계열도 후보로 반환."""
        used = [g[4] for g in self.raingages.values()
                if len(g) >= 5 and g[3].upper() == "TIMESERIES"]
        others = [n for n in self.timeseries if n not in used]
        return used + others

    def current_rain_series(self) -> str | None:
        names = self.rain_series_names()
        return names[0] if names else None

    def rain_depth_mm(self, series: str) -> float | None:
        """강우 시계열의 총 강우량(mm). 강도형(INTENSITY)만 계산."""
        gage = next(iter(self.raingages.values()), None)
        data = self.timeseries.get(series)
        if not gage or not data:
            return None
        fmt = gage[0].upper()
        vals = [v for _, v in data]
        if fmt == "VOLUME":
            total = sum(vals)
        elif fmt == "CUMULATIVE":
            total = max(vals) if vals else 0.0
        else:  # INTENSITY: 값(in/hr 또는 mm/hr) × 간격(hr)
            h = _hours(gage[1])
            total = sum(vals) * h
        return self.depth_to_mm(total)

    def rain_hyetograph(self, series: str) -> list[tuple[float, float]]:
        """(시작 후 경과 분, 강우강도 mm/hr) 목록."""
        gage = next(iter(self.raingages.values()), None)
        data = self.timeseries.get(series, [])
        if not gage:
            return []
        fmt = gage[0].upper()
        h = _hours(gage[1]) or 1.0
        out = []
        prev = 0.0
        for t, v in data:
            minutes = _hours(t) * 60.0
            if fmt == "VOLUME":
                inten = v / h
            elif fmt == "CUMULATIVE":
                inten = (v - prev) / h
                prev = v
            else:
                inten = v
            out.append((minutes, self.depth_to_mm(inten)))
        return out

    def rain_at_interval(self, series: str, step_min: float) -> list[float]:
        """강우 시계열을 강우계 간격(step_min)의 강우강도(mm/hr) 목록으로 변환 (계단형)."""
        hy = self.rain_hyetograph(series)
        if not hy:
            return []
        end = hy[-1][0]
        n = int(round(end / step_min)) + 1
        out, j = [], 0
        for k in range(n):
            t = k * step_min
            while j + 1 < len(hy) and hy[j + 1][0] <= t + 1e-9:
                j += 1
            out.append(hy[j][1])
        return out

    # ---------------------------------------------------------- 원지반 침투
    def native_infil_rate(self) -> float | None:
        """면적가중 원지반 침투율(모형 단위: in/hr 또는 mm/hr).

        Horton → 종기침투능(MinRate), Green-Ampt → 포화투수계수(Ksat).
        Curve Number 방식이면 None.
        """
        tot_a, tot = 0.0, 0.0
        for name, vals in self.infiltration.items():
            sc = self.subcatchments.get(name)
            if not sc:
                continue
            method = self.infil_method
            # SWMM 5.2: 줄 끝에 개별 방식이 붙을 수 있음
            if vals and not _is_number(vals[-1]):
                method = vals[-1].upper()
                vals = vals[:-1]
            try:
                if "HORTON" in method:
                    rate = float(vals[1])
                elif "GREEN" in method:
                    rate = float(vals[1])
                else:
                    continue
            except (IndexError, ValueError):
                continue
            tot += rate * sc.area
            tot_a += sc.area
        return tot / tot_a if tot_a > 0 else None

    # ---------------------------------------------------------- 관망 위상
    def downstream_length(self) -> dict[str, float]:
        """각 소유역 출구에서 방류구까지 하류 방향 관로 길이 합."""
        out_edges = defaultdict(list)
        for lk in self.links.values():
            out_edges[lk.from_node].append((lk.to_node, lk.length))
        memo: dict[str, float] = {}

        def dist(node: str, seen=()) -> float:
            if node in memo:
                return memo[node]
            if node in seen:
                return 0.0
            edges = out_edges.get(node)
            if not edges:
                d = 0.0
            else:
                d = max(length + dist(nxt, seen + (node,)) for nxt, length in edges)
            memo[node] = d
            return d

        res = {}
        for sc in self.subcatchments.values():
            outlet, hops = sc.outlet, 0
            while outlet in self.subcatchments and hops < 50:
                outlet = self.subcatchments[outlet].outlet
                hops += 1
            res[sc.name] = dist(outlet)
        return res

    def upstream_downstream_groups(self) -> tuple[list[str], list[str]]:
        d = self.downstream_length()
        if not d:
            return [], []
        ordered = sorted(d, key=lambda k: -d[k])
        half = (len(ordered) + 1) // 2
        return ordered[:half], ordered[half:]

    def total_area(self) -> float:
        return sum(s.area for s in self.subcatchments.values())

    def mean_imperv(self) -> float:
        a = self.total_area()
        return sum(s.area * s.imperv for s in self.subcatchments.values()) / a if a else 0.0


def _is_number(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False


def _hours(hhmm: str) -> float:
    """'0:05', '1:30', '0.25' → 시간 단위 실수."""
    s = str(hhmm).strip()
    if ":" in s:
        parts = [float(p) for p in s.split(":")]
        while len(parts) < 3:
            parts.append(0.0)
        return parts[0] + parts[1] / 60.0 + parts[2] / 3600.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def load_model(inp: InpFile) -> Model:
    flow_units = (inp.get_option("FLOW_UNITS", "CFS") or "CFS").upper()
    us = flow_units in US_FLOW_UNITS

    subs = {}
    for r in inp.rows("SUBCATCHMENTS"):
        if len(r) < 7:
            continue
        subs[r[0]] = Subcatchment(r[0], r[1], r[2], float(r[3]), float(r[4]),
                                  float(r[5]), float(r[6]), r[7:])

    node_types = {}
    for sec, kind in [("JUNCTIONS", "JUNCTION"), ("OUTFALLS", "OUTFALL"),
                      ("STORAGE", "STORAGE"), ("DIVIDERS", "DIVIDER")]:
        for r in inp.rows(sec):
            node_types[r[0]] = kind

    links = {}
    for r in inp.rows("CONDUITS"):
        links[r[0]] = Link(r[0], "CONDUIT", r[1], r[2], float(r[3]))
    for sec, kind in [("PUMPS", "PUMP"), ("ORIFICES", "ORIFICE"),
                      ("WEIRS", "WEIR"), ("OUTLETS", "OUTLET")]:
        for r in inp.rows(sec):
            links[r[0]] = Link(r[0], kind, r[1], r[2], 0.0)

    coords = {r[0]: (float(r[1]), float(r[2])) for r in inp.rows("COORDINATES") if len(r) >= 3}
    vertices: dict[str, list] = defaultdict(list)
    for r in inp.rows("VERTICES"):
        if len(r) >= 3:
            vertices[r[0]].append((float(r[1]), float(r[2])))
    polygons: dict[str, list] = defaultdict(list)
    for r in inp.rows("POLYGONS"):
        if len(r) >= 3:
            polygons[r[0]].append((float(r[1]), float(r[2])))

    gages = {r[0]: r[1:] for r in inp.rows("RAINGAGES")}

    ts: dict[str, list] = defaultdict(list)
    for r in inp.rows("TIMESERIES"):
        if len(r) >= 2 and r[1].upper() == "FILE":
            continue
        # 형식: name [date] time value [time value ...]
        toks = r[1:]
        if toks and "/" in toks[0]:
            toks = toks[1:]
        try:
            for k in range(0, len(toks) - 1, 2):
                ts[r[0]].append((toks[k], float(toks[k + 1])))
        except ValueError:
            continue

    infil_method = (inp.get_option("INFILTRATION", "HORTON") or "HORTON").upper()
    infil = {r[0]: r[1:] for r in inp.rows("INFILTRATION")}

    return Model(inp, flow_units, us, subs, node_types, links, coords,
                 dict(vertices), dict(polygons), gages, dict(ts), infil_method, infil)
