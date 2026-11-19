"""SWMM .inp 파일을 섹션 단위로 읽고 고쳐 쓰는 최소 파서.

수정하지 않는 섹션은 원문 그대로 보존한다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_SECTION_RE = re.compile(r"^\s*\[([^\]]+)\]")


def _strip_comment(line: str) -> str:
    """';' 이후 주석 제거 (따옴표 안의 ';'는 고려하지 않음 – 수정 대상 섹션에는 없음)."""
    i = line.find(";")
    return line if i < 0 else line[:i]


@dataclass
class Section:
    name: str                      # 대문자 섹션 이름 (예: 'SUBCATCHMENTS')
    header: str                    # 원래 헤더 줄 (예: '[SUBCATCHMENTS]')
    lines: list[str] = field(default_factory=list)


class InpFile:
    def __init__(self, text: str):
        self.preamble: list[str] = []
        self.sections: list[Section] = []
        cur: Section | None = None
        for raw in text.splitlines():
            m = _SECTION_RE.match(raw)
            if m:
                cur = Section(m.group(1).strip().upper(), raw.strip())
                self.sections.append(cur)
            elif cur is None:
                self.preamble.append(raw)
            else:
                cur.lines.append(raw)

    # ------------------------------------------------------------------ 기본
    @classmethod
    def from_file(cls, path) -> "InpFile":
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return cls(f.read())

    def copy(self) -> "InpFile":
        return InpFile(self.to_text())

    def has(self, name: str) -> bool:
        return self._find(name) is not None

    def _find(self, name: str) -> Section | None:
        name = name.upper()
        for s in self.sections:
            if s.name == name:
                return s
        return None

    def rows(self, name: str) -> list[list[str]]:
        """데이터 줄을 공백 기준 토큰 목록으로 반환 (주석·빈 줄 제외)."""
        s = self._find(name)
        if s is None:
            return []
        out = []
        for line in s.lines:
            body = _strip_comment(line).strip()
            if body:
                out.append(body.split())
        return out

    def set_rows(self, name: str, rows: list[list], comment: str | None = None,
                 after: list[str] | None = None) -> None:
        """섹션 내용을 rows로 교체. 섹션이 없으면 `after`에 적힌 섹션 뒤(없으면 끝)에 새로 만든다."""
        lines = []
        if comment:
            lines.append(";;" + comment)
        for r in rows:
            lines.append(" ".join(_fmt(v) for v in r))
        lines.append("")
        s = self._find(name)
        if s is not None:
            s.lines = lines
            return
        new = Section(name.upper(), f"[{name.upper()}]", lines)
        idx = len(self.sections)
        for a in (after or []):
            j = self._index(a)
            if j is not None:
                idx = j + 1
                break
        self.sections.insert(idx, new)

    def append_rows(self, name: str, rows: list[list]) -> None:
        """섹션 끝(빈 줄 앞)에 행을 추가. 섹션이 없으면 새로 만든다."""
        s = self._find(name)
        if s is None:
            self.set_rows(name, rows)
            return
        insert_at = len(s.lines)
        while insert_at > 0 and not s.lines[insert_at - 1].strip():
            insert_at -= 1
        for k, r in enumerate(rows):
            s.lines.insert(insert_at + k, " ".join(_fmt(v) for v in r))

    def remove(self, name: str) -> None:
        s = self._find(name)
        if s is not None:
            self.sections.remove(s)

    def _index(self, name: str) -> int | None:
        name = name.upper()
        for i, s in enumerate(self.sections):
            if s.name == name:
                return i
        return None

    # --------------------------------------------------------------- OPTIONS
    def get_option(self, key: str, default: str | None = None) -> str | None:
        key = key.upper()
        for r in self.rows("OPTIONS"):
            if r and r[0].upper() == key:
                return " ".join(r[1:]) if len(r) > 1 else ""
        return default

    def set_option(self, key: str, value: str) -> None:
        key = key.upper()
        s = self._find("OPTIONS")
        if s is None:
            self.set_rows("OPTIONS", [[key, value]])
            return
        for i, line in enumerate(s.lines):
            body = _strip_comment(line).strip()
            if body and body.split()[0].upper() == key:
                s.lines[i] = f"{key:<20} {value}"
                return
        # 마지막 빈 줄 앞에 추가
        insert_at = len(s.lines)
        while insert_at > 0 and not s.lines[insert_at - 1].strip():
            insert_at -= 1
        s.lines.insert(insert_at, f"{key:<20} {value}")

    # ------------------------------------------------------------------ 출력
    def to_text(self) -> str:
        out = list(self.preamble)
        for s in self.sections:
            out.append(s.header)
            out.extend(s.lines)
        text = "\n".join(out)
        if not text.endswith("\n"):
            text += "\n"
        return text

    def write(self, path) -> None:
        with open(path, "w", encoding="utf-8") as f:
            f.write(self.to_text())


def _fmt(v) -> str:
    if isinstance(v, float):
        if v == 0:
            return "0"
        if abs(v) >= 1e5 or abs(v) < 1e-3:
            return f"{v:.6g}"
        return f"{v:.6f}".rstrip("0").rstrip(".")
    return str(v)
