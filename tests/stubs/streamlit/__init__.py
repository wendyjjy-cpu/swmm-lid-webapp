"""시험용 Streamlit 대역: 위젯은 기본값(또는 ST_OVERRIDES 환경변수의 값)을 돌려준다."""
import contextlib
import json
import os

_OV = json.loads(os.environ.get("ST_OVERRIDES", "{}"))
CALLS = []


class StopException(Exception):
    pass


class _SessionState(dict):
    def __getattr__(self, k):
        try:
            return self[k]
        except KeyError as e:
            raise AttributeError(k) from e

    def __setattr__(self, k, v):
        self[k] = v


session_state = _SessionState()


def _rec(name, *a, **k):
    CALLS.append((name, a, k))


class _Widgets:
    # ---- 출력
    def title(self, *a, **k): _rec("title", *a)
    def header(self, *a, **k): _rec("header", *a)
    def subheader(self, *a, **k): _rec("subheader", *a)
    def caption(self, *a, **k): _rec("caption", *a)
    def markdown(self, *a, **k): _rec("markdown", *a)
    def write(self, *a, **k): _rec("write", *a)
    def info(self, *a, **k): _rec("info", *a)
    def warning(self, *a, **k): _rec("warning", *a)
    def error(self, *a, **k): _rec("error", *a)
    def metric(self, label, value, delta=None, **k): _rec("metric", label, value, delta)

    def dataframe(self, df, **k):
        _rec("dataframe", df)

    def plotly_chart(self, fig, **k):
        _rec("plotly_chart", fig)

    def download_button(self, label, data, **k):
        _rec("download_button", label, len(data))
        return False

    # ---- 입력
    def _ov(self, label, default):
        return _OV.get(label, default)

    def selectbox(self, label, options, index=0, format_func=str, **k):
        options = list(options)
        for o in options:
            format_func(o)
        return self._ov(label, options[index] if options else None)

    def radio(self, label, options, index=0, format_func=str, **k):
        options = list(options)
        for o in options:
            format_func(o)
        return self._ov(label, options[index])

    def slider(self, label, min_value=None, max_value=None, value=None, step=None, **k):
        return self._ov(label, value)

    def select_slider(self, label, options, value=None, **k):
        return self._ov(label, value if value is not None else list(options)[0])

    def toggle(self, label, value=False, **k):
        return self._ov(label, value)

    def multiselect(self, label, options, default=None, **k):
        return self._ov(label, list(default or []))

    def number_input(self, label, min_value=None, max_value=None, value=None, step=None, **k):
        return self._ov(label, value)

    def button(self, label, **k):
        return self._ov(label, False)

    def data_editor(self, df, **k):
        v = self._ov(k.get("key", "data_editor"), None)
        if v is not None:
            import pandas as pd
            return pd.DataFrame({df.columns[0]: v})
        return df.copy()

    def file_uploader(self, label, **k):
        v = self._ov(label, None)
        if v is None:
            return None

        class _F:
            def __init__(self, p): self.p = p
            def getvalue(self):
                with open(self.p, "rb") as f:
                    return f.read()
        return _F(v)

    @contextlib.contextmanager
    def expander(self, *a, **k):
        yield self

    def columns(self, spec, **k):
        n = spec if isinstance(spec, int) else len(spec)
        return [_Widgets() for _ in range(n)]

    def progress(self, *a, **k):
        class _P:
            def progress(self, *a, **k): pass
            def empty(self): pass
        return _P()

    @contextlib.contextmanager
    def spinner(self, *a, **k):
        yield

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


_root = _Widgets()
sidebar = _Widgets()

for _n in dir(_root):
    if not _n.startswith("__"):
        globals()[_n] = getattr(_root, _n)


def set_page_config(**k): _rec("set_page_config")


def tabs(names):
    return [_Widgets() for _ in names]


def stop():
    raise StopException()


def rerun():
    raise StopException()


def _passthrough_decorator(*dargs, **dkw):
    if dargs and callable(dargs[0]) and not dkw:
        return dargs[0]

    def deco(f):
        return f
    return deco


cache_data = _passthrough_decorator
cache_resource = _passthrough_decorator
