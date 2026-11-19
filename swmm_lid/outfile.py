"""SWMM 5 이진 결과 파일(.out) 읽기 (EPA SWMM output.c 형식)."""
from __future__ import annotations

import struct
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np

MAGIC = 516114522

SUBCATCH_VARS = ["rainfall", "snow_depth", "evap", "infil", "runoff",
                 "gw_flow", "gw_elev", "soil_moisture"]
NODE_VARS = ["depth", "head", "volume", "lateral_inflow", "total_inflow", "flooding"]
LINK_VARS = ["flow", "depth", "velocity", "volume", "capacity"]
SYS_VARS = ["air_temp", "rainfall", "snow_depth", "infil", "runoff", "dw_inflow",
            "gw_inflow", "rdii_inflow", "ext_inflow", "total_inflow", "flooding",
            "outflow", "storage", "evap", "pet"]


@dataclass
class OutFile:
    version: int
    flow_units: int
    subcatchments: list[str]
    nodes: list[str]
    links: list[str]
    pollutants: list[str]
    times: list[datetime]
    _sub: np.ndarray      # (period, subcatch, var)
    _node: np.ndarray
    _link: np.ndarray
    _sys: np.ndarray      # (period, var)
    report_start: datetime | None = None   # 보고 시작 시각 (첫 기록은 한 보고 간격 뒤)

    def subcatch_series(self, name: str, var: str) -> np.ndarray:
        return self._sub[:, self.subcatchments.index(name), SUBCATCH_VARS.index(var)]

    def node_series(self, name: str, var: str) -> np.ndarray:
        return self._node[:, self.nodes.index(name), NODE_VARS.index(var)]

    def link_series(self, name: str, var: str) -> np.ndarray:
        return self._link[:, self.links.index(name), LINK_VARS.index(var)]

    def system_series(self, var: str) -> np.ndarray:
        return self._sys[:, SYS_VARS.index(var)]

    def minutes(self) -> np.ndarray:
        if not self.times:
            return np.array([])
        t0 = self.report_start or self.times[0]
        return np.round(np.array([(t - t0).total_seconds() / 60.0 for t in self.times]), 3)


def _swmm_date(x: float) -> datetime:
    # SWMM 날짜: 1899-12-30 기준 일수 (델파이/엑셀 방식)
    return datetime(1899, 12, 30) + timedelta(days=x)


def read_out(path: str) -> OutFile:
    with open(path, "rb") as f:
        data = f.read()
    if len(data) < 24 * 2:
        raise ValueError("결과 파일이 비어 있습니다.")
    id_pos, prop_pos, res_pos, nper, err, magic2 = struct.unpack("<6i", data[-24:])
    magic, version, flow_units, n_sub, n_node, n_link, n_poll = struct.unpack("<7i", data[:28])
    if magic != MAGIC or magic2 != MAGIC:
        raise ValueError("SWMM 결과 파일 형식이 아닙니다.")
    if err:
        raise ValueError(f"SWMM 실행 오류 코드 {err}")

    pos = id_pos

    def read_ids(n):
        nonlocal pos
        ids = []
        for _ in range(n):
            (ln,) = struct.unpack_from("<i", data, pos)
            pos += 4
            ids.append(data[pos:pos + ln].decode("utf-8", errors="replace"))
            pos += ln
        return ids

    subs = read_ids(n_sub)
    nodes = read_ids(n_node)
    links = read_ids(n_link)
    polls = read_ids(n_poll)

    # 보고 변수 개수 (물성 영역 뒤에 위치) — 물성 영역을 건너뛰어 읽는다
    pos = prop_pos
    for n_obj in (n_sub, n_node, n_link):
        (n_props,) = struct.unpack_from("<i", data, pos)
        pos += 4 + 4 * n_props + 4 * n_props * n_obj
    counts = []
    for _ in range(4):
        (nv,) = struct.unpack_from("<i", data, pos)
        pos += 4 + 4 * nv
        counts.append(nv)
    nsv, nnv, nlv, nsys = counts
    (start_d,) = struct.unpack_from("<d", data, pos)
    report_start = _swmm_date(start_d)

    per_vals = n_sub * nsv + n_node * nnv + n_link * nlv + nsys
    rec = 8 + 4 * per_vals
    times = []
    vals = np.empty((nper, per_vals), dtype=np.float32)
    for k in range(nper):
        off = res_pos + k * rec
        (d,) = struct.unpack_from("<d", data, off)
        times.append(_swmm_date(d))
        vals[k] = np.frombuffer(data, dtype="<f4", count=per_vals, offset=off + 8)

    a = 0
    sub = vals[:, a:a + n_sub * nsv].reshape(nper, n_sub, nsv); a += n_sub * nsv
    node = vals[:, a:a + n_node * nnv].reshape(nper, n_node, nnv); a += n_node * nnv
    link = vals[:, a:a + n_link * nlv].reshape(nper, n_link, nlv); a += n_link * nlv
    sysv = vals[:, a:a + nsys]
    return OutFile(version, flow_units, subs, nodes, links, polls, times, sub, node, link, sysv,
                   report_start)
