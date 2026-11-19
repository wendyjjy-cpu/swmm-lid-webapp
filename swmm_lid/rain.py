"""설계강우 시간분포: 총강우량·지속시간 → 강우계 간격별 강우강도(mm/hr)."""
from __future__ import annotations

# Huff, F. A. (1990). Time Distributions of Heavy Rainstorms in Illinois.
# Illinois State Water Survey Circular 173, Table 3 (점강우, 10 mi² 이하 유역의 중앙값(50%) 곡선).
# 강우 지속시간 0, 10, ..., 100% 시점의 누가 강우량(%)
HUFF_POINT_MEDIAN = {
    1: [0, 33, 52, 66, 75, 82, 86, 90, 94, 97, 100],
    2: [0, 8, 16, 29, 51, 70, 81, 88, 93, 97, 100],
    3: [0, 6, 12, 19, 27, 38, 57, 79, 89, 95, 100],
    4: [0, 5, 10, 16, 22, 28, 35, 45, 59, 84, 100],
}

DISTRIBUTIONS = {
    "Huff 1분위": 1,
    "Huff 2분위": 2,
    "Huff 3분위": 3,
    "Huff 4분위": 4,
    "균등": 0,
}


def _cum_fraction(curve: list[float], t: float) -> float:
    """무차원 시간 t(0~1)의 누가 강우 비율(0~1), 10% 간격 값 사이는 선형 보간."""
    t = min(max(t, 0.0), 1.0)
    x = t * 10.0
    i = min(int(x), 9)
    a = x - i
    return (curve[i] + (curve[i + 1] - curve[i]) * a) / 100.0


def design_hyetograph(total_mm: float, duration_min: float, step_min: float,
                      distribution: str = "Huff 3분위") -> list[float]:
    """강우계 간격(step_min)별 강우강도(mm/hr). 합계 × 간격 = total_mm."""
    n = max(1, int(round(duration_min / step_min)))
    q = DISTRIBUTIONS.get(distribution, 3)
    out = []
    for k in range(n):
        if q == 0:
            frac = 1.0 / n
        else:
            curve = HUFF_POINT_MEDIAN[q]
            frac = _cum_fraction(curve, (k + 1) / n) - _cum_fraction(curve, k / n)
        out.append(total_mm * frac / (step_min / 60.0))
    return out
