from .graph_objects import Figure


def make_subplots(rows=1, cols=1, **k):
    f = Figure()
    f.layout["_subplots"] = (rows, cols, k)
    return f
