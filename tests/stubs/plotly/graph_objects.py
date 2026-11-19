"""시험용 Plotly 대역: 트레이스와 레이아웃 인자를 dict로 기록."""


class _Trace(dict):
    def __init__(self, **k):
        super().__init__(type=self.__class__.__name__.lower(), **k)


class Scatter(_Trace):
    pass


class Bar(_Trace):
    pass


class Figure:
    def __init__(self, data=None, **k):
        self.data = []
        if data is not None:
            self.data = list(data) if isinstance(data, (list, tuple)) else [data]
        self.layout = {}
        self.axes = []

    def add_trace(self, tr, row=None, col=None):
        self.data.append(tr)
        return self

    def update_layout(self, **k):
        self.layout.update(k)
        return self

    def update_xaxes(self, **k):
        self.axes.append(("x", k))
        return self

    def update_yaxes(self, **k):
        self.axes.append(("y", k))
        return self
