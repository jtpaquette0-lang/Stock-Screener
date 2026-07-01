"""Headless universe-cache rebuild.

Run by the daily GitHub Action (and usable locally). It reuses app.py's real
fetch logic via a minimal Streamlit stub, so there is no code duplication or
drift — whatever the live app does to build a row, this does too.

Usage:
    # PowerShell:  $env:FMP_API_KEY="your_key"; python rebuild_cache.py
    # bash:        FMP_API_KEY=your_key python rebuild_cache.py
"""
import os
import sys
import types
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

if not os.environ.get("FMP_API_KEY"):
    sys.exit("FMP_API_KEY not set in the environment.")
os.environ.setdefault("ANTHROPIC_API_KEY", "")

# ── Minimal Streamlit stub so we can load app.py's functions without a UI ──
st = types.ModuleType("streamlit")
class _SS(dict):
    def __getattr__(self, k):
        try:
            return self[k]
        except KeyError:
            raise AttributeError(k)
    def __setattr__(self, k, v):
        self[k] = v
st.session_state = _SS()
st.secrets = {}
_noop = lambda *a, **k: None
st.set_page_config = _noop
st.markdown = _noop
class _W:
    progress = _noop
    markdown = _noop
    empty = _noop
st.progress = lambda *a, **k: _W()
st.empty = lambda *a, **k: _W()
st.stop = _noop
st.rerun = _noop
st.error = _noop
sys.modules["streamlit"] = st

# ── Load app.py's functions (everything up to the ROUTING section) ──
_src = open("app.py", encoding="utf-8").read()
_src = _src[:_src.index("# ROUTING")]
A = {}
exec(compile(_src, "app.py", "exec"), A)


def main():
    fmp = A["fmp"]
    sp  = [x["symbol"] for x in (fmp("sp500-constituent") or []) if x.get("symbol")]
    nq  = [x["symbol"] for x in (fmp("nasdaq-constituent") or []) if x.get("symbol")]
    scr = A["fetch_screener_universe"]()
    tim = {}
    for s in sp:
        tim.setdefault(s, set()).add("S&P 500")
    for s in nq:
        tim.setdefault(s, set()).add("NASDAQ 100")
    tickers = list(dict.fromkeys(sp + nq + scr))
    print(f"universe: {len(tickers)} tickers "
          f"(sp500={len(sp)} nq100={len(nq)} screener={len(scr)})", flush=True)

    emap = A["fetch_earnings_map"]()
    print(f"earnings calendar: {len(emap)} symbols", flush=True)

    universe, done, t0 = [], 0, time.time()
    with ThreadPoolExecutor(max_workers=12) as ex:
        futures = {ex.submit(A["fetch_universe_row"], s): s for s in tickers}
        for fut in as_completed(futures):
            sym = futures[fut]
            done += 1
            if done % 250 == 0:
                print(f"  {done}/{len(tickers)}  ({(time.time()-t0)/60:.1f} min)", flush=True)
            try:
                row = fut.result()
            except Exception:
                row = None
            if row:
                e = emap.get(sym, {})
                row["next_earnings_date"]    = e.get("date", "")
                row["next_earnings_eps_est"] = e.get("epsEstimated")
                row["next_earnings_time"]    = e.get("time", "")
                row["indexes"] = list(tim.get(sym, set()))
                universe.append(row)

    # Deduplicate dual-class shares by company name (keep higher market cap).
    seen, deduped = {}, []
    for row in universe:
        name = (row.get("name") or row["symbol"]).strip().lower()
        try:
            mc = float(row.get("mkt_cap_raw") or 0)
        except (TypeError, ValueError):
            mc = 0
        if name not in seen:
            seen[name] = (len(deduped), mc)
            deduped.append(row)
        else:
            idx, prev = seen[name]
            if mc > prev:
                seen[name] = (idx, mc)
                deduped[idx] = row

    # SAFETY GUARD: never overwrite a good cache with a broken/empty rebuild
    # (e.g. missing FMP_API_KEY, or the API being down). Fail loudly instead so
    # the workflow's commit step is skipped and the existing cache is preserved.
    if len(deduped) < 500:
        sys.exit(f"ABORT: only {len(deduped)} companies built — refusing to overwrite "
                 f"the cache. Check that FMP_API_KEY is set and the API is reachable.")

    A["universe_cache_save"](deduped, tim)
    print(f"DONE — {len(deduped)} companies cached in {(time.time()-t0)/60:.1f} min", flush=True)


if __name__ == "__main__":
    main()
