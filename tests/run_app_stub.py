"""app.py를 Streamlit/Plotly 대역으로 실행해 예외 없이 끝까지 도는지 확인."""
import json, os, runpy, sys, traceback
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests", "stubs"))
sys.path.insert(1, ROOT)
os.chdir(ROOT)
import streamlit as st

cases = json.loads(sys.argv[1]) if len(sys.argv) > 1 else [{}]
ok = True
for i, ov in enumerate(cases):
    st._OV.clear(); st._OV.update(ov); st.CALLS.clear()
    keep = ov.pop("_keep_state", False)
    if not keep:
        st.session_state.clear()
    try:
        runpy.run_path(os.path.join(ROOT, "app.py"), run_name="__main__")
        status = "완료"
    except st.StopException:
        status = "st.stop()"
    except Exception:
        ok = False
        status = "예외\n" + traceback.format_exc()
    kinds = {}
    for c in st.CALLS:
        kinds[c[0]] = kinds.get(c[0], 0) + 1
    print(f"[case {i}] {status} | {kinds}")
    for c in st.CALLS:
        if c[0] in ("error", "warning"):
            print("   ", c[0], c[1][:1])
    if ov.get("_show_metrics"):
        for c in st.CALLS:
            if c[0] == "metric": print("   metric", c[1])
            if c[0] == "dataframe" and ov.get("_show_df"): print(c[1][0].head(12).to_string())
sys.exit(0 if ok else 1)
