"""SWMM 계산 엔진 호출.

배포 환경: swmm-toolkit 패키지(EPA SWMM 5.2 엔진을 파이썬에서 호출)를 사용한다.
개발·시험 환경: 환경변수 SWMM_LIB(libswmm5 공유 라이브러리) 또는
SWMM_EXE(runswmm 실행 파일)로 같은 EPA 엔진을 직접 호출할 수 있다.

SWMM 엔진은 전역 상태를 쓰므로 한 프로세스에서 동시에 두 모의를 돌리면 안 된다.
여러 사용자가 동시에 접속해도 안전하도록 잠금(lock)으로 한 번에 하나씩 실행한다.
"""
from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import threading

_LOCK = threading.Lock()
_BACKEND = None


class SwmmError(RuntimeError):
    pass


def _toolkit_backend():
    from swmm.toolkit import solver  # type: ignore

    # swmm-toolkit 0.15~0.17: 실행 함수 이름은 swmm_run (공식 시험 코드 기준).
    # 혹시 이름이 바뀐 판에 대비해 run도 찾는다.
    _run = getattr(solver, "swmm_run", None) or getattr(solver, "run", None)
    if _run is None:
        raise ImportError("swmm.toolkit.solver에 실행 함수가 없습니다.")

    def run(inp, rpt, out):
        _run(inp, rpt, out)

    def version():
        for name in ("swmm_get_version", "swmm_getVersion", "swmm_version"):
            fn = getattr(solver, name, None)
            if fn is not None:
                try:
                    v = int(fn())                 # 예: 52004 → 5.2.4
                    return f"{v // 10000}.{(v // 1000) % 10}.{v % 1000}"
                except Exception:
                    continue
        return "5.2"

    return "swmm-toolkit", run, version


class _QuietStdout:
    """엔진이 콘솔에 찍는 진행 메시지를 숨긴다."""

    def __enter__(self):
        import sys
        sys.stdout.flush()
        self.fd = os.dup(1)
        self.null = os.open(os.devnull, os.O_WRONLY)
        os.dup2(self.null, 1)

    def __exit__(self, *a):
        os.dup2(self.fd, 1)
        os.close(self.fd)
        os.close(self.null)
        return False


def _ctypes_backend(path):
    lib = ctypes.CDLL(path)
    lib.swmm_run.argtypes = [ctypes.c_char_p] * 3
    lib.swmm_run.restype = ctypes.c_int

    def run(inp, rpt, out):
        with _QuietStdout():
            code = lib.swmm_run(inp.encode(), rpt.encode(), out.encode())
        if code > 0:
            raise SwmmError(f"SWMM 오류 코드 {code}")

    def version():
        try:
            v = int(lib.swmm_getVersion())
            return f"{v // 10000}.{(v // 1000) % 10}.{v % 1000}"
        except Exception:
            return "unknown"

    return "libswmm5 (ctypes)", run, version


def _exe_backend(path):
    def run(inp, rpt, out):
        proc = subprocess.run([path, inp, rpt, out], capture_output=True, text=True, timeout=600)
        if proc.returncode != 0:
            raise SwmmError(proc.stdout[-2000:] + proc.stderr[-2000:])

    def version():
        return "runswmm"

    return "runswmm (실행 파일)", run, version


def get_backend():
    global _BACKEND
    if _BACKEND is not None:
        return _BACKEND
    errors = []
    try:
        _BACKEND = _toolkit_backend()
        return _BACKEND
    except Exception as e:  # ImportError 등
        errors.append(f"swmm-toolkit: {e}")
    lib = os.environ.get("SWMM_LIB")
    if lib and os.path.exists(lib):
        try:
            _BACKEND = _ctypes_backend(lib)
            return _BACKEND
        except OSError as e:
            errors.append(f"SWMM_LIB: {e}")
    exe = os.environ.get("SWMM_EXE") or shutil.which("runswmm")
    if exe and os.path.exists(exe):
        _BACKEND = _exe_backend(exe)
        return _BACKEND
    raise SwmmError("SWMM 엔진을 찾지 못했습니다. `pip install swmm-toolkit`을 실행하세요. "
                    + " / ".join(errors))


def backend_name() -> str:
    try:
        return get_backend()[0]
    except SwmmError:
        return "없음"


def engine_version() -> str:
    try:
        return get_backend()[2]()
    except Exception:
        return "unknown"


def run_swmm(inp_path: str, rpt_path: str, out_path: str) -> None:
    name, run, _ = get_backend()
    with _LOCK:
        try:
            run(inp_path, rpt_path, out_path)
        except SwmmError:
            raise
        except Exception as e:
            raise SwmmError(str(e)) from e
