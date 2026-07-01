import streamlit as st
import requests
import pandas as pd
import re
import os
import time
import json
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    import yfinance as yf
    YF = True
except ImportError:
    YF = False

try:
    import plotly.graph_objects as go
    PLOTLY = True
except ImportError:
    PLOTLY = False

# ─────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────
def _load_secret(name, default=""):
    """Read a secret from .streamlit/secrets.toml first, then the
    environment. Keeps API keys out of the source code."""
    try:
        if name in st.secrets:
            return st.secrets[name]
    except Exception:
        pass  # no secrets.toml present — fall through to env var
    return os.environ.get(name, default)

API_KEY           = _load_secret("FMP_API_KEY")        # Financial Modeling Prep
ANTHROPIC_API_KEY = _load_secret("ANTHROPIC_API_KEY")
BASE     = "https://financialmodelingprep.com/stable"
DEV_MODE = False   # True = 40-ticker dev set · False = full universe
# Universe = S&P 500 + NASDAQ 100 + every US-listed operating company above this
# market-cap floor (via FMP company-screener). Lower = wider net / longer load.
UNIVERSE_MARKETCAP_FLOOR = 1_000_000_000   # $1B
PORTFOLIO_FILE  = "portfolio.json"
UNIVERSE_CACHE  = "universe_cache.json"

METRIC_DEFS = {
    "Revenue Growth (YoY)": dict(tf="YoY Annual", field="rev_growth",     higher=True,  fmt="pct",
        source="FMP",
        tip="Year-over-year revenue growth. Measures how fast the top line is expanding. Sustained >15% suggests strong competitive positioning. Peter Lynch considered anything above 20% a strong signal."),
    "ROIC": dict(tf="TTM", field="roic",            higher=True,  fmt="pct",
        source="FMP",
        tip="Return on Invested Capital — Buffett's #1 quality metric. Measures how efficiently management turns invested capital into profit. Above 15% typically signals a durable competitive advantage (moat). Below 8% is a red flag."),
    "Gross Margin": dict(tf="TTM", field="gross_margin",   higher=True,  fmt="pct",
        source="FMP",
        tip="Gross Profit as a % of Revenue. Shows the raw profitability of the product or service before operating expenses. Software companies: >70% is excellent. Manufacturing: >30% is solid."),
    "Operating Margin": dict(tf="TTM", field="op_margin",      higher=True,  fmt="pct",
        source="FMP",
        tip="Operating Income as a % of Revenue. Shows how much profit is left after all operating costs. Above 20% is strong for most sectors. Expanding margins over time = growing pricing power."),
    "Net Margin": dict(tf="TTM", field="net_margin",     higher=True,  fmt="pct",
        source="FMP",
        tip="Net Income as a % of Revenue — the bottom line. Above 15% is excellent. Compare to peers in the same sector as benchmarks vary widely."),
    "ROE": dict(tf="TTM", field="roe",             higher=True,  fmt="pct",
        source="FMP/Yahoo",
        tip="Return on Equity. Measures how much profit is generated with shareholders' money. Above 15% is healthy. Buffett looks for consistently high ROE without excessive debt leverage."),
    "Earnings Yield": dict(tf="TTM", field="earnings_yield", higher=True,  fmt="pct",
        source="FMP",
        tip="EBIT / Enterprise Value — Joel Greenblatt's key metric from his Magic Formula. The inverse of EV/EBIT. Higher = better value. Above 8% is attractive; compare to the 10-year Treasury yield as a benchmark."),
    "FCF Yield": dict(tf="TTM", field="fcf_yield",      higher=True,  fmt="pct",
        source="FMP",
        tip="Free Cash Flow / Enterprise Value. Shows how much real cash the business generates relative to its price. Above 4% is solid. FCF is harder to manipulate than net income, making this a high-quality valuation metric."),
    "PEG Ratio": dict(tf="TTM", field="peg",             higher=False, fmt="x",
        source="FMP/Yahoo",
        tip="P/E Ratio divided by the earnings growth rate. Peter Lynch's core metric. Under 1.0 = potentially undervalued relative to growth. 1.0–2.0 = fair for quality growth. Above 2.0 = expensive. A lower PEG is better."),
    "P/E Ratio": dict(tf="TTM", field="pe",              higher=False, fmt="x",
        source="FMP/Yahoo",
        tip="Price divided by Earnings per Share. Lower = cheaper. Context matters heavily by sector. Tech: 20–35x is typical. Consumer staples: 15–25x. Always compare within the same industry."),
    "EV/EBITDA": dict(tf="TTM", field="ev_ebitda",       higher=False, fmt="x",
        source="FMP",
        tip="Enterprise Value divided by EBITDA. The most widely used cross-sector valuation multiple. Lower = cheaper. Below 15x is attractive for most sectors. Above 25x requires strong growth justification."),
    "Debt/Equity": dict(tf="TTM", field="debt_equity",    higher=False, fmt="x",
        source="FMP/Yahoo",
        tip="Total Debt relative to Shareholders' Equity. Lower = safer balance sheet. Above 2x warrants scrutiny. High debt amplifies both gains and losses. Buffett prefers companies that could repay all debt in 3–4 years from earnings."),
    "Rev 2yr CAGR": dict(tf="2-Yr CAGR", field="rev_cagr",       higher=True,  fmt="pct",
        source="FMP",
        tip="2-year Compound Annual Growth Rate for revenue. Smooths out one-year anomalies to reveal the underlying growth trend. More reliable than single-year growth for identifying durable compounders."),
    "Current Ratio": dict(tf="TTM", field="current_ratio",  higher=True,  fmt="x",
        source="FMP",
        tip="Current Assets divided by Current Liabilities. Measures short-term liquidity. Above 1.5x is healthy. Below 1.0x means more short-term obligations than liquid assets — potential liquidity risk."),
    "Interest Coverage": dict(tf="TTM", field="int_coverage",   higher=True,  fmt="x",
        source="FMP",
        tip="EBIT divided by Interest Expense. How many times the company can cover its interest payments from operating profit. Above 5x is comfortable. Below 2x is a serious red flag."),
}

C = {
    "bg":     "#0e1015",   # near-black charcoal
    "bg2":    "#151b27",   # card surface
    "bg3":    "#1c2438",   # secondary / badge bg
    "border": "rgba(255,255,255,0.07)",  # ultra-subtle border
    "border2":"rgba(255,255,255,0.13)",
    "text":   "#ffffff",
    "dim":    "#c8d8ee",   # light — legible on charcoal
    "muted":  "#8899b8",   # medium — still visible on charcoal
    "green":  "#00d084",   # electric green — finance terminal style
    "amber":  "#f0b429",   # warm amber
    "red":    "#f65b5b",   # soft red
    "blue":   "#4f8ef7",   # softer blue accent
    "blue_bg":"#0a1628",   # deep blue hero bg
    "blue_border":"#1d4ed8",
}


# ─────────────────────────────────────────────────────────────
# PORTFOLIO — per-user storage: Supabase (persistent, hosted) when configured,
# else a local JSON file (dev / single-user). All pf_* helpers below call
# pf_load / pf_save, so they inherit whichever backend is active.
# ─────────────────────────────────────────────────────────────
def _current_user():
    """The logged-in username (set by the auth gate). Falls back to 'local' for
    single-user file mode when no login is configured."""
    try:
        return st.session_state.get("pf_user") or "local"
    except Exception:
        return "local"

_SUPABASE = None
_SUPABASE_TRIED = False
def _supabase():
    """Lazily create (and cache) the Supabase client if URL+key are configured."""
    global _SUPABASE, _SUPABASE_TRIED
    if _SUPABASE_TRIED:
        return _SUPABASE
    _SUPABASE_TRIED = True
    url = _load_secret("SUPABASE_URL")
    key = _load_secret("SUPABASE_KEY")
    if url and key:
        try:
            from supabase import create_client
            _SUPABASE = create_client(url, key)
        except Exception:
            _SUPABASE = None
    return _SUPABASE

def pf_load():
    """Load the current user's portfolio. Cached in session_state so repeated
    calls within a render don't hit the DB on every widget."""
    user = _current_user()
    if st.session_state.get("pf_cache_user") == user and "pf_cache" in st.session_state:
        return st.session_state["pf_cache"]
    companies = []
    sb = _supabase()
    if sb:
        try:
            res = sb.table("portfolios").select("companies").eq("username", user).execute()
            if res.data:
                companies = res.data[0].get("companies") or []
        except Exception:
            companies = []
    else:
        try:
            with open(PORTFOLIO_FILE, "r") as f:
                companies = json.load(f).get("companies", [])
        except Exception:
            companies = []
    st.session_state["pf_cache"] = companies
    st.session_state["pf_cache_user"] = user
    return companies

def pf_save(companies):
    """Persist the current user's portfolio, then refresh the session cache."""
    user = _current_user()
    sb = _supabase()
    if sb:
        try:
            sb.table("portfolios").upsert(
                {"username": user, "companies": companies}).execute()
        except Exception:
            pass
    else:
        try:
            with open(PORTFOLIO_FILE, "w") as f:
                json.dump({"companies": companies}, f, indent=2, default=str)
        except Exception:
            pass
    st.session_state["pf_cache"] = companies
    st.session_state["pf_cache_user"] = user

def pf_in_portfolio(symbol):
    return any(c["symbol"]==symbol for c in pf_load())

def pf_add(symbol, universe_data):
    companies = pf_load()
    row = next((r for r in (universe_data or []) if r["symbol"]==symbol), {})
    existing = next((c for c in companies if c["symbol"]==symbol), None)
    snap = {k:v for k,v in row.items() if k not in ("description",)}
    if existing:
        existing["data"] = snap
        existing["name"] = row.get("name", existing.get("name", symbol))
    else:
        companies.append({
            "symbol":     symbol,
            "name":       row.get("name", symbol),
            "sector":     row.get("sector","—"),
            "added_date": datetime.now().strftime("%Y-%m-%d"),
            "notes":      "",
            "data":       snap,
        })
    pf_save(companies); return companies

def pf_remove(symbol):
    companies = [c for c in pf_load() if c["symbol"]!=symbol]
    pf_save(companies); return companies

def pf_save_notes(symbol, notes):
    companies = pf_load()
    for c in companies:
        if c["symbol"]==symbol: c["notes"]=notes; break
    pf_save(companies)

def universe_cache_save(universe, ticker_index_map):
    try:
        with open(UNIVERSE_CACHE, "w") as f:
            json.dump({
                "universe": universe,
                "ticker_index_map": {k: list(v) for k, v in ticker_index_map.items()}
            }, f, default=str)
    except: pass

def universe_cache_load():
    try:
        with open(UNIVERSE_CACHE, "r") as f:
            data = json.load(f)
        universe = data.get("universe", [])
        tim_raw  = data.get("ticker_index_map", {})
        ticker_index_map = {k: set(v) for k, v in tim_raw.items()}
        if universe:
            return universe, ticker_index_map
    except: pass
    return None, None

def pf_refresh(universe_data):
    """Called after universe loads — update data for portfolio companies in universe."""
    companies = pf_load()
    if not companies or not universe_data: return
    changed = False
    for c in companies:
        row = next((r for r in universe_data if r["symbol"]==c["symbol"]), None)
        if row:
            c["data"]   = {k:v for k,v in row.items() if k not in ("description",)}
            c["name"]   = row.get("name",   c.get("name",   c["symbol"]))
            c["sector"] = row.get("sector", c.get("sector", "—"))
            changed = True
    if changed: pf_save(companies)

# ─────────────────────────────────────────────────────────────
# PAGE CONFIG
# ─────────────────────────────────────────────────────────────
st.set_page_config(page_title="Stock Screener", page_icon="📈",
                   layout="wide", initial_sidebar_state="expanded")


st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=Inter:wght@400;500;600&display=swap');
html,body,[class*="css"]{{font-family:'Inter',sans-serif;}}
.stApp{{background:{C['bg']};color:{C['text']};}}
/* ── Sidebar: darker grey panel with bold, high-contrast text ── */
section[data-testid="stSidebar"],
section[data-testid="stSidebar"] > div,
div[data-testid="stSidebar"],
div[data-testid="stSidebar"] > div,
[data-testid="stSidebarContent"],
[data-testid="stSidebarUserContent"]{{background:#262b35!important;border-right:1px solid rgba(255,255,255,0.08);}}
/* All sidebar text near-white for contrast against the grey panel */
section[data-testid="stSidebar"] *{{color:#eef2f7!important;}}
/* Headings, captions, and labels — bold */
section[data-testid="stSidebar"] h1,section[data-testid="stSidebar"] h2,
section[data-testid="stSidebar"] h3,section[data-testid="stSidebar"] p,
section[data-testid="stSidebar"] label,section[data-testid="stSidebar"] label p{{font-weight:700!important;}}
/* Metric checkbox labels — larger and extra bold */
section[data-testid="stSidebar"] .stCheckbox label,
section[data-testid="stSidebar"] .stCheckbox label span{{font-size:15px!important;font-weight:800!important;}}
/* Radio options (Highest / Lowest / Range) */
section[data-testid="stSidebar"] [role="radiogroup"] *{{font-weight:600!important;font-size:13px!important;}}
/* Dropdown controls (Index / Sector) — dark input to match the panel */
section[data-testid="stSidebar"] div[data-baseweb="select"] > div{{background:#1b2029!important;border-color:rgba(255,255,255,0.18)!important;}}
/* Number inputs (Range Min/Max) — dark field so the digits and +/- show */
section[data-testid="stSidebar"] div[data-testid="stNumberInput"] div[data-baseweb="input"]{{background:#1b2029!important;border-color:rgba(255,255,255,0.18)!important;}}
section[data-testid="stSidebar"] div[data-testid="stNumberInput"] input{{background:#1b2029!important;color:#eef2f7!important;-webkit-text-fill-color:#eef2f7!important;font-weight:700!important;}}
section[data-testid="stSidebar"] div[data-testid="stNumberInput"] button{{background:#2a313d!important;}}
section[data-testid="stSidebar"] div[data-testid="stNumberInput"] button:hover{{background:#3a424f!important;}}
section[data-testid="stSidebar"] div[data-testid="stNumberInput"] button svg{{fill:#eef2f7!important;color:#eef2f7!important;}}
.stButton>button{{background:{C['blue_border']};color:#fff;border:none;border-radius:6px;
                  font-weight:500;padding:8px 18px;font-size:13px;}}
.stButton>button:hover{{background:#388bfd;}}
.stTabs [data-baseweb="tab"]{{background:{C['bg2']};color:{C['dim']};border-radius:6px 6px 0 0;}}
.stTabs [aria-selected="true"]{{background:{C['bg3']};color:{C['text']}!important;}}
.stDataFrame{{font-size:12px;}}
footer{{visibility:hidden;}}
header[data-testid="stHeader"]{{background:{C["bg"]}!important;border-bottom:none!important;}}
#MainMenu{{visibility:hidden;}}
div[data-testid="stToolbar"]{{visibility:hidden;}}
/* Hide the sidebar close button so it stays open permanently */
[data-testid="stSidebarCollapseButton"]{{display:none!important;}}
button[data-testid="collapsedControl"]{{display:none!important;}}

div[data-testid="stSelectbox"] label,
div[data-testid="stSelectbox"] p {{color:#ffffff!important;font-size:13px!important;}}
div[data-testid="stRadio"] label p {{color:#ffffff!important;}}
.stDownloadButton>button{{background:#1f6feb!important;color:#ffffff!important;
    border:none!important;font-weight:600!important;}}
.stDownloadButton>button:hover{{background:#388bfd!important;}}
/* Form submit buttons (e.g. the Log in button) — blue bg, visible white text */
[data-testid="stFormSubmitButton"] button,
.stForm button[kind="secondaryFormSubmit"],
.stForm button{{background:{C['blue_border']}!important;color:#ffffff!important;
    border:none!important;border-radius:6px!important;font-weight:600!important;
    padding:9px 18px!important;}}
[data-testid="stFormSubmitButton"] button *,
.stForm button *{{color:#ffffff!important;}}
[data-testid="stFormSubmitButton"] button:hover,
.stForm button:hover{{background:#388bfd!important;}}
/* Portfolio add button — dark bg with green text always visible */
[data-testid="stButton"] button[kind="secondary"]{{
    background:#1a2332!important;color:#00d084!important;
    border:1px solid rgba(0,208,132,0.4)!important;font-weight:500!important;
}}
[data-testid="stButton"] button[kind="secondary"]:hover{{
    background:#00d084!important;color:#0e1015!important;
    border-color:#00d084!important;
}}
.tt{{position:relative;display:inline-block;}}
.tt .tb{{visibility:hidden;opacity:0;background:#151b27;color:#ffffff;
        font-size:11px;line-height:1.65;padding:10px 13px;border-radius:8px;
        border:1px solid #2563eb66;position:absolute;z-index:9999;
        bottom:130%;left:50%;transform:translateX(-50%);min-width:230px;
        max-width:310px;box-shadow:0 8px 24px rgba(0,0,0,.7);
        transition:opacity .15s;pointer-events:none;white-space:normal;
        font-family:Inter,sans-serif;font-weight:400;}}
.tt:hover .tb{{visibility:visible;opacity:1;}}
.tt .tb::after{{content:\'\';position:absolute;top:100%;left:50%;
               transform:translateX(-50%);border:5px solid transparent;
               border-top-color:#2563eb66;}}
</style>""", unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────
# SESSION STATE INIT
# ─────────────────────────────────────────────────────────────
for k, v in [("page","screener"),("selected_symbol",None),
             ("universe_data",None),("ranked_data",None),
             ("selected_metrics",[]),("scan_done",False),
             ("universe_loaded",False),("metric_states",None),
             ("portfolio_page",False),
             ("hl_priority_order",[]),("range_priority_order",[]),
             ("scan_results",[]),("secondary_rankings",{}),
             ("filter_run_id",0)]:
    if k not in st.session_state:
        st.session_state[k] = v

# Restore from cache on browser refresh (session cleared but file persists)
if not st.session_state.universe_loaded:
    _cached_univ, _cached_tim = universe_cache_load()
    if _cached_univ:
        st.session_state.universe_data   = _cached_univ
        st.session_state.universe_loaded = True
        st.session_state["ticker_index_map"] = _cached_tim
        pf_refresh(_cached_univ)

# ─────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────
def fmp(endpoint, params={}, _retries=4):
    """FMP GET with retry-and-backoff. FMP rate-limits (429) under heavy parallel
    load — without retries a throttled call silently drops that company from the
    universe. We retry 429s and transient errors with increasing backoff so the
    bulk rebuild keeps the full universe instead of thinning out."""
    p = dict(params); p["apikey"] = API_KEY
    for attempt in range(_retries):
        try:
            r = requests.get(f"{BASE}/{endpoint}", params=p, timeout=20)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(1.0 * (attempt + 1))   # rate-limited / server hiccup — back off
                continue
            return {}                              # 4xx (bad key, not found) — no point retrying
        except Exception:
            time.sleep(0.5 * (attempt + 1))        # network hiccup — retry
    return {}

def first(raw):
    if isinstance(raw,list): return raw[0] if raw else {}
    return raw if isinstance(raw,dict) else {}

# ─────────────────────────────────────────────────────────────
# BATCH FETCH HELPERS
# ─────────────────────────────────────────────────────────────
def batch_fetch(endpoint, symbols, batch_size=100, extra_params={}):
    """Fetch a single-record-per-symbol endpoint in batches.
       Returns dict keyed by symbol."""
    results = {}
    for i in range(0, len(symbols), batch_size):
        batch = symbols[i:i+batch_size]
        p = dict(extra_params)
        p["symbol"] = ",".join(batch)
        data = fmp(endpoint, p)
        if isinstance(data, list):
            for item in data:
                sym = item.get("symbol")
                if sym and sym not in results:
                    results[sym] = item
        elif isinstance(data, dict) and data.get("symbol"):
            results[data["symbol"]] = data
        time.sleep(0.15)
    return results

def batch_fetch_income(symbols, batch_size=50, limit=3):
    """Batch fetch income statements. Returns dict of lists keyed by symbol."""
    results = {}
    for i in range(0, len(symbols), batch_size):
        batch = symbols[i:i+batch_size]
        data = fmp("income-statement", {"symbol": ",".join(batch), "limit": limit})
        if isinstance(data, list):
            for item in data:
                sym = item.get("symbol")
                if sym:
                    if sym not in results:
                        results[sym] = []
                    if len(results[sym]) < limit:
                        results[sym].append(item)
        time.sleep(0.15)
    return results

def build_row_from_parts(sym, prof, km, rat, inc_list):
    """Build a screener row from pre-fetched data parts (no API calls)."""
    if not prof and not km and not rat:
        return None

    rev_growth = rev_cagr = eps_growth = None
    if len(inc_list) >= 2:
        r0 = gf(inc_list[0],"revenue") or 0
        r1 = gf(inc_list[1],"revenue") or 0
        if r1: rev_growth = (r0-r1)/abs(r1)*100
    if len(inc_list) >= 3:
        r0 = gf(inc_list[0],"revenue") or 0
        r2 = gf(inc_list[2],"revenue") or 0
        if r2 and r2>0: rev_cagr = ((r0/r2)**0.5-1)*100
    # Flag pre-revenue / early companies (rev < $50M in the current OR prior year).
    # Their revenue-growth % is computed off a near-zero base (Joby $0.1M→$53M =
    # +39,000%), which distorts growth screens — excluded by default, opt-in to show.
    _rn = gf(inc_list[0], "revenue") if inc_list else None
    _rp = gf(inc_list[1], "revenue") if len(inc_list) >= 2 else None
    _revs = [x for x in (_rn, _rp) if x is not None]
    pre_revenue = (not _revs) or (min(_revs) < 50_000_000)
    if len(inc_list) >= 2:
        e0 = gf(inc_list[0],"eps","epsdiluted") or 0
        e1 = gf(inc_list[1],"eps","epsdiluted") or 0
        if e1 and e1!=0: eps_growth = (e0-e1)/abs(e1)*100

    roic  = as_pct(gf(km,"roicTTM","returnOnInvestedCapitalTTM","roic"))
    if not roic and inc_list:
        ebit_v = gf(inc_list[0],"operatingIncome","ebit")
        tax_v  = gf(inc_list[0],"incomeTaxExpense")
        pbi_v  = gf(inc_list[0],"incomeBeforeTax")
        ta_v   = gf(rat,"totalAssetsTurnoverTTM") or gf(km,"tangibleAssetValueTTM")
        if ebit_v and pbi_v and pbi_v != 0:
            eff_tax = abs(tax_v/pbi_v) if tax_v else 0.21
            roic = as_pct(gf(km,"roicTTM")) # retry
    if not roic:
        roic = as_pct(gf(rat,"returnOnCapitalEmployedTTM"))

    roe   = as_pct(gf(km,"returnOnEquityTTM") or gf(rat,"returnOnEquityTTM") or gf(km,"roeTTM"))
    roa   = as_pct(gf(km,"returnOnAssetsTTM") or gf(rat,"returnOnAssetsTTM"))
    gm    = as_pct(gf(rat,"grossProfitMarginTTM"))
    op_m  = as_pct(gf(rat,"operatingProfitMarginTTM"))
    net_m = as_pct(gf(rat,"netProfitMarginTTM"))
    ey    = as_pct(gf(km,"earningsYieldTTM"))
    fcfy  = as_pct(gf(km,"freeCashFlowYieldTTM"))
    pe    = (gf(rat,"priceToEarningsRatioTTM","priceEarningsRatioTTM","peRatioTTM")
             or gf(km,"peRatioTTM"))
    peg   = (gf(rat,"priceToEarningsGrowthRatioTTM","priceEarningsToGrowthRatioTTM")
             or gf(km,"pegRatioTTM"))
    # Sanitize absurd ratios. Fresh spinoffs / stub tickers (e.g. FDXF) get broken
    # per-share data from FMP — a 42,000% earnings yield or a P/E of -0.005 — which
    # would otherwise top the screen. Reject values outside any real-world range.
    if ey  is not None and abs(ey)  > 100: ey  = None    # >100% yield = data error
    if fcfy is not None and abs(fcfy) > 100: fcfy = None
    if pe  is not None and abs(pe)  < 0.3: pe  = None     # near-zero P/E = data error
    if peg is not None and abs(peg) > 100: peg = None
    eveb  = (gf(km,"evToEBITDATTM","enterpriseValueOverEBITDATTM")
             or gf(rat,"enterpriseValueMultipleTTM"))
    de    = gf(rat,"debtEquityRatioTTM","debtToEquityRatioTTM") or gf(km,"debtToEquityTTM")
    if de is None:
        # gf() drops zeros, but debt/equity == 0 is a REAL value (a debt-free
        # company) and a positive signal — recover it explicitly.
        for _src, _k in ((rat,"debtToEquityRatioTTM"), (rat,"debtEquityRatioTTM"),
                         (km,"debtToEquityTTM")):
            _v = _src.get(_k)
            if _v is not None:
                try:
                    if float(_v) == 0: de = 0.0
                except (TypeError, ValueError): pass
                break
    cr    = gf(rat,"currentRatioTTM")
    ic    = gf(rat,"interestCoverageRatioTTM","interestCoverageTTM") or gf(km,"interestCoverageTTM")
    if not ic and inc_list:
        ebit_v = gf(inc_list[0],"operatingIncome","ebit")
        int_v  = gf(inc_list[0],"interestExpense")
        if ebit_v and int_v and int_v != 0:
            ic = abs(ebit_v/int_v)
        elif ebit_v and (not int_v or int_v == 0):
            ic = 9999  # No interest expense = no debt = sentinel for display
    pfcf  = (gf(rat,"priceToFreeCashFlowRatioTTM")
             or gf(km,"pfcfRatioTTM","priceToFreeCashFlowsRatioTTM","priceToFreeCashFlowRatioTTM"))
    if not pfcf:
        fcf_ps = gf(km,"freeCashFlowPerShareTTM")
        px_raw = gf(prof,"price")
        if fcf_ps and px_raw and float(fcf_ps)>0:
            pfcf = float(px_raw)/float(fcf_ps)
    pb    = gf(rat,"priceToBookRatioTTM") or gf(km,"pbRatioTTM")
    mc    = gf(prof,"marketCap","mktCap") or gf(km,"marketCap","marketCapTTM")
    price = gf(prof,"price")

    return {
        "symbol":sym,
        "name":   prof.get("companyName", sym),
        "sector": prof.get("sector","—"),
        "industry":prof.get("industry","—"),
        "mkt_cap":fm(mc), "mkt_cap_raw":mc,
        "price":  f"${price:.2f}" if price else "—",
        "ceo":    prof.get("ceo",""),
        "employees": prof.get("fullTimeEmployees"),
        "website":prof.get("website",""),
        "description":(prof.get("description","") or "")[:600],
        # screening fields
        "rev_growth":rev_growth, "rev_cagr":rev_cagr, "roic":roic,
        "gross_margin":gm, "op_margin":op_m, "net_margin":net_m,
        "roe":roe, "earnings_yield":ey, "fcf_yield":fcfy,
        "peg":peg, "pe":pe, "ev_ebitda":eveb, "debt_equity":de,
        "current_ratio":cr, "int_coverage":ic,
        "roa":roa, "eps_growth":eps_growth, "pfcf":pfcf, "pb":pb,
        "pre_revenue":pre_revenue,
    }

def fetch_earnings_map(days_ahead=100):
    """FMP earnings-calendar → {symbol: {date, epsEstimated, time}} (earliest
    upcoming per symbol) for the next `days_ahead` days. Replaces ~500 per-ticker
    yfinance lookups during the bulk load.

    The endpoint caps at 4000 rows per call, so a wide window silently drops peak
    earnings-season dates. We fetch in ~7-day base chunks and ADAPTIVELY split any
    chunk that comes back at the cap (down to single days) so nothing is lost."""
    from datetime import date as _d, timedelta as _td
    today   = _d.today()
    today_s = today.strftime("%Y-%m-%d")
    end_all = today + _td(days=days_ahead)
    out = {}

    def absorb(data):
        if not isinstance(data, list):
            return
        for e in data:
            sym = e.get("symbol")
            dt  = (e.get("date") or "")[:10]
            if not sym or not dt or dt < today_s:
                continue
            if sym not in out or dt < out[sym]["date"]:   # earliest upcoming per symbol
                out[sym] = {"date": dt,
                            "epsEstimated": e.get("epsEstimated"),
                            "time": e.get("time", "")}

    def fetch_range(a, b, depth=0):
        data = fmp("earnings-calendar",
                   {"from": a.strftime("%Y-%m-%d"), "to": b.strftime("%Y-%m-%d")})
        # A full 4000-row response means the window was truncated — split & retry.
        if isinstance(data, list) and len(data) >= 4000 and (b - a).days >= 1 and depth < 7:
            mid = a + _td(days=(b - a).days // 2)
            fetch_range(a, mid, depth + 1)
            fetch_range(mid + _td(days=1), b, depth + 1)
        else:
            absorb(data)
        time.sleep(0.05)

    start = today
    while start < end_all:
        stop = min(start + _td(days=7), end_all)
        fetch_range(start, stop)
        start = stop + _td(days=1)
    return out

def _yf_backfill_row(row, sym):
    """Fill fields FMP left blank using yfinance. Called ONLY for companies with
    gaps (not the whole universe), so it stays fast and avoids mass rate-limiting."""
    if not YF:
        return row
    try:
        ydata = yf.Ticker(sym).info or {}
    except Exception:
        return row
    if not ydata:
        return row

    def setmiss(field, val):
        if val is not None and row.get(field) is None:
            row[field] = val
    def yp(k):
        v = ydata.get(k)
        return as_pct(v) if v is not None else None

    setmiss("roic",          yp("returnOnInvestedCapital"))
    setmiss("roe",           yp("returnOnEquity"))
    setmiss("roa",           yp("returnOnAssets"))
    setmiss("gross_margin",  yp("grossMargins"))
    setmiss("op_margin",     yp("operatingMargins"))
    setmiss("net_margin",    yp("profitMargins"))
    setmiss("pe",            ydata.get("trailingPE") or ydata.get("forwardPE"))
    _peg = ydata.get("pegRatio") or ydata.get("trailingPegRatio")
    setmiss("peg",           _peg if (_peg and _peg > 0) else None)
    setmiss("ev_ebitda",     ydata.get("enterpriseToEbitda"))
    _de = ydata.get("debtToEquity")
    setmiss("debt_equity",   (_de / 100.0) if _de is not None else None)
    setmiss("current_ratio", ydata.get("currentRatio"))
    setmiss("pb",            ydata.get("priceToBook"))
    setmiss("rev_growth",    yp("revenueGrowth"))
    setmiss("eps_growth",    yp("earningsGrowth"))
    if row.get("earnings_yield") is None:
        _pe = row.get("pe") or ydata.get("trailingPE")
        if _pe and float(_pe) != 0:
            row["earnings_yield"] = 100.0 / float(_pe)
    if row.get("fcf_yield") is None:
        _mc  = row.get("mkt_cap_raw") or ydata.get("marketCap")
        _fcf = ydata.get("freeCashflow")
        if _mc and _fcf and float(_mc) != 0:
            row["fcf_yield"] = float(_fcf) / float(_mc) * 100
    if row.get("mkt_cap_raw") is None and ydata.get("marketCap"):
        row["mkt_cap_raw"] = ydata["marketCap"]; row["mkt_cap"] = fm(ydata["marketCap"])
    if not row.get("price") or row.get("price") == "—":
        px = ydata.get("currentPrice") or ydata.get("regularMarketPrice")
        if px: row["price"] = f"${px:.2f}"
    if not row.get("employees"):
        row["employees"] = ydata.get("fullTimeEmployees")
    if not row.get("website"):
        row["website"] = ydata.get("website", "")
    if not row.get("description"):
        row["description"] = (ydata.get("longBusinessSummary", "") or "")[:600]
    return row

# Core screening fields FMP should populate for a large cap; if ≥2 are missing
# we consider the row gappy and let yfinance backfill it.
_CORE_FIELDS = ["pe", "roic", "gross_margin", "net_margin", "rev_growth",
                "ev_ebitda", "pb", "earnings_yield", "fcf_yield"]

def fetch_screener_universe(market_cap_floor=UNIVERSE_MARKETCAP_FLOOR):
    """Broad opportunity universe from FMP's company-screener: US-listed common
    stocks (no ETFs/funds) above a market-cap floor. Returns a list of symbols.
    This is what lets us catch smaller companies before they enter an index."""
    data = fmp("company-screener", {
        "marketCapMoreThan": int(market_cap_floor),
        "country": "US",
        "isEtf": "false",
        "isFund": "false",
        "isActivelyTrading": "true",
        "limit": 6000,
    })
    syms = []
    if isinstance(data, list):
        for x in data:
            if x.get("exchangeShortName") not in ("NASDAQ", "NYSE", "AMEX"):
                continue
            s = x.get("symbol")
            if not s:
                continue
            # Skip mutual funds — 5-letter symbols ending in X (e.g. FOCKX). FMP's
            # isFund flag misses these, and they carry no company fundamentals.
            if re.match(r"^[A-Z]{4}X$", s):
                continue
            syms.append(s)
    return syms

def _is_operating_company(row):
    """Separate real operating companies (incl. pre-revenue biotech/tech) from the
    ETFs / mutual funds / notes that slip past the screener. Robust layered checks:
      1. Reject mutual-fund tickers (5 letters ending in X, e.g. BALFX) — these
         carry a CIK and management-company headcount, so those signals don't help.
      2. Reject unambiguous fund/ETF names (no real company is 'iShares …').
      3. Keep anything with real income-statement data (0.0 counts, for pre-revenue).
      4. Else keep only if it has a workforce (pre-revenue companies do; funds don't)."""
    sym  = (row.get("symbol") or "").upper()
    name = (row.get("name") or "").lower()
    if re.match(r"^[A-Z]{4}X$", sym):
        return False
    if any(k in name for k in ("etf", " etn", "ishares", "proshares",
                               "direxion", "spdr", " index fund")):
        return False
    if any(row.get(f) is not None
           for f in ("net_margin", "gross_margin", "op_margin", "rev_growth")):
        return True
    return bool(row.get("employees"))

def fetch_universe_row(sym):
    """Hybrid row build for the bulk universe load: FMP first (fast, thread-safe),
    then a yfinance backfill ONLY when FMP left the row with real gaps. Complete
    large-cap rows never touch yfinance, so the full universe stays ~fast."""
    prof = first(fmp("profile",         {"symbol": sym}))
    km   = first(fmp("key-metrics-ttm", {"symbol": sym}))
    rat  = first(fmp("ratios-ttm",      {"symbol": sym}))
    inc  = fmp("income-statement",      {"symbol": sym, "limit": 3})
    if not isinstance(inc, list):
        inc = []
    row = build_row_from_parts(sym, prof, km, rat, inc)
    if row is None:
        # FMP returned nothing usable — skeleton row, let yfinance fill it in.
        row = {"symbol": sym, "name": prof.get("companyName", sym),
               "sector": prof.get("sector", "—"), "industry": prof.get("industry", "—"),
               "mkt_cap": "—", "mkt_cap_raw": None, "price": "—",
               "ceo": prof.get("ceo", ""), "employees": prof.get("fullTimeEmployees"),
               "website": prof.get("website", ""),
               "description": (prof.get("description", "") or "")[:600],
               "rev_growth": None, "rev_cagr": None, "roic": None, "gross_margin": None,
               "op_margin": None, "net_margin": None, "roe": None, "earnings_yield": None,
               "fcf_yield": None, "peg": None, "pe": None, "ev_ebitda": None,
               "debt_equity": None, "current_ratio": None, "int_coverage": None,
               "roa": None, "eps_growth": None, "pfcf": None, "pb": None}
    row["cik"] = str(prof.get("cik") or prof.get("CIK") or "").strip()

    missing = sum(1 for f in _CORE_FIELDS if row.get(f) is None)
    if missing >= 2 or not row.get("mkt_cap_raw"):
        row = _yf_backfill_row(row, sym)
    # Drop non-operating instruments (ETFs / closed-end funds / notes) that slip
    # through the screener — they have no fundamentals to screen on.
    if not _is_operating_company(row):
        return None
    return row

def gf(d, *keys):
    for k in keys:
        v = d.get(k)
        if v is not None and v != "" and v != 0:
            try:
                f = float(v)
                if f != 0: return f
            except: pass
    return None

def as_pct(v):
    if v is None: return None
    return v*100 if abs(v) < 5 else v

def fp(v, d=1):
    if v is None: return "—"
    try:
        f=float(v)
        return "—" if f!=f else f"{f:.{d}f}%"
    except: return "—"
def fx(v, d=1):
    if v is None: return "—"
    try:
        f=float(v)
        if f!=f: return "—"
        if f >= 9999: return "No Debt ✓"
        if f == -9999: return "N/A"
        return f"{f:.{d}f}×"
    except: return "—"
def fm(v):
    if v is None: return "—"
    try:
        v=float(v)
        if abs(v)>=1e12: return f"${v/1e12:.2f}T"
        if abs(v)>=1e9:  return f"${v/1e9:.2f}B"
        if abs(v)>=1e6:  return f"${v/1e6:.2f}M"
        return f"${v:,.0f}"
    except: return "—"

def vc(v, good, bad, inv=False):
    if v is None: return C["dim"]
    if inv: return C["green"] if v<=good else (C["red"] if v>=bad else C["amber"])
    return C["green"] if v>=good else (C["red"] if v<=bad else C["amber"])

def tooltip_badge(tip_text):
    safe = tip_text.replace("<","&lt;").replace(">","&gt;").replace('"','&quot;')
    bg   = C['bg3']; dim = C['dim']
    return (
        f"<span class='tt' style='display:inline-block;margin-left:5px;"
        f"vertical-align:middle;'>"
        f"<span style='display:inline-flex;align-items:center;justify-content:center;"
        f"width:15px;height:15px;border-radius:50%;background:{bg};color:{dim};"
        f"font-size:9px;font-weight:700;cursor:help;'>?</span>"
        f"<span class='tb'>{safe}</span>"
        f"</span>"
    )

def metric_row(label, val_str, color, source=None, tip=None, tf=None):
    src_html = (f"<span style='font-size:9px;padding:1px 5px;border-radius:4px;"
                f"background:{C['bg3']};color:{C['muted']};margin-left:6px;"
                f"vertical-align:middle;'>{source}</span>") if source else ""
    tip_html  = tooltip_badge(tip) if tip else ""
    return (
        f"<div style='display:flex;justify-content:space-between;align-items:center;"
        f"padding:5px 0;border-bottom:1px solid {C['border']};'>"
        f"<div style='display:flex;align-items:center;'>"
        f"<span style='font-size:12px;color:#ffffff;'>{label}</span>"
        f"{tip_html}{src_html}</div>"
        f"<span style='font-family:IBM Plex Mono,monospace;font-size:12px;"
        f"font-weight:500;color:{color};'>{val_str}</span>"
        f"</div>"
    )

def section_header(icon, title):
    return (f"<div style='font-size:11px;font-weight:600;text-transform:uppercase;"
            f"letter-spacing:1px;color:{C['muted']};padding:10px 0 8px 0;"
            f"border-top:1px solid {C['border']};margin-top:14px;'>{icon} {title}</div>")

# ─────────────────────────────────────────────────────────────
# DATA FETCHING
# ─────────────────────────────────────────────────────────────
def fetch_ticker(sym):
    km   = first(fmp("key-metrics-ttm",  {"symbol":sym}))
    rat  = first(fmp("ratios-ttm",       {"symbol":sym}))
    prof = first(fmp("profile",          {"symbol":sym}))
    inc  = fmp("income-statement",       {"symbol":sym,"limit":3})
    if not isinstance(inc,list): inc=[]

    # yfinance fallback
    ydata = {}
    if YF:
        try:
            t = yf.Ticker(sym)
            ydata = t.info or {}
        except: pass

    def yf_pct(key): return as_pct(ydata.get(key))

    # Revenue growth
    rev_growth = rev_cagr = eps_growth = None
    if len(inc)>=2:
        r0=gf(inc[0],"revenue") or 0; r1=gf(inc[1],"revenue") or 0
        if r1: rev_growth=(r0-r1)/abs(r1)*100
    if len(inc)>=3:
        r0=gf(inc[0],"revenue") or 0; r2=gf(inc[2],"revenue") or 0
        if r2 and r2>0: rev_cagr=((r0/r2)**0.5-1)*100
    if len(inc)>=2:
        e0=gf(inc[0],"eps","epsdiluted") or 0; e1=gf(inc[1],"eps","epsdiluted") or 0
        if e1 and e1!=0: eps_growth=(e0-e1)/abs(e1)*100

    # ── All metrics: FMP primary, yfinance fallback on every field ──────────
    roic   = (as_pct(gf(km,"roicTTM","returnOnInvestedCapitalTTM","roic"))
              or as_pct(gf(rat,"returnOnCapitalEmployedTTM"))
              or yf_pct("returnOnInvestedCapital"))
    roe    = as_pct(gf(rat,"returnOnEquityTTM") or gf(km,"roeTTM"))
    if not roe:
        _roe_yf = ydata.get("returnOnEquity")
        if _roe_yf is not None and _roe_yf != 0:
            roe = as_pct(_roe_yf)
    # Manual calc fallback: Net Income / Shareholders Equity
    if not roe and inc:
        _ni  = gf(inc[0], "netIncome")
        _eq  = ydata.get("bookValue")
        _shs = ydata.get("sharesOutstanding")
        if _ni and _eq and _shs and float(_eq)*float(_shs) != 0:
            try: roe = (float(_ni) / (float(_eq) * float(_shs))) * 100
            except: pass
    roa    = (as_pct(gf(rat,"returnOnAssetsTTM") or gf(km,"returnOnAssetsTTM"))
              or yf_pct("returnOnAssets"))
    gm     = as_pct(gf(rat,"grossProfitMarginTTM")) or yf_pct("grossMargins")
    op_m   = as_pct(gf(rat,"operatingProfitMarginTTM")) or yf_pct("operatingMargins")
    net_m  = as_pct(gf(rat,"netProfitMarginTTM")) or yf_pct("profitMargins")

    # Earnings yield: FMP only, fallback calculate from PE
    ey = as_pct(gf(km,"earningsYieldTTM"))
    if not ey:
        _pe = gf(rat,"priceEarningsRatioTTM","peRatioTTM") or gf(km,"peRatioTTM") or ydata.get("trailingPE")
        if _pe and float(_pe) != 0: ey = 100.0 / float(_pe)

    # FCF yield: FMP only, fallback calculate from market cap + fcf
    fcfy = as_pct(gf(km,"freeCashFlowYieldTTM"))
    if not fcfy:
        _mc  = gf(prof,"mktCap") or gf(km,"marketCapTTM") or ydata.get("marketCap")
        _fcf = ydata.get("freeCashflow")
        if _mc and _fcf and float(_mc) != 0:
            fcfy = (float(_fcf) / float(_mc)) * 100

    pe     = (gf(rat,"priceEarningsRatioTTM","peRatioTTM")
              or gf(km,"peRatioTTM")
              or ydata.get("trailingPE")
              or ydata.get("forwardPE"))
    _peg_fmp = gf(km,"pegRatioTTM") or gf(rat,"priceEarningsToGrowthRatioTTM")
    _peg_yf  = ydata.get("pegRatio") or ydata.get("trailingPegRatio")
    peg = _peg_fmp or (_peg_yf if _peg_yf and _peg_yf > 0 else None)
    eveb   = (gf(km,"enterpriseValueOverEBITDATTM")
              or gf(rat,"enterpriseValueMultipleTTM")
              or ydata.get("enterpriseToEbitda"))

    # Debt/equity: yfinance reports as percentage (e.g. 150 = 1.5x), divide by 100
    _de_yf = ydata.get("debtToEquity")
    de     = (gf(rat,"debtEquityRatioTTM","debtToEquityRatioTTM")
              or gf(km,"debtToEquityTTM")
              or ((_de_yf / 100.0) if _de_yf is not None else None))

    cr     = gf(rat,"currentRatioTTM") or ydata.get("currentRatio")

    ic     = gf(rat,"interestCoverageTTM") or gf(km,"interestCoverageTTM")
    if not ic and inc:
        ebit_v = gf(inc[0],"operatingIncome","ebit")
        int_v  = gf(inc[0],"interestExpense")
        if ebit_v and int_v and int_v != 0:
            ic = abs(ebit_v / int_v)
        elif ebit_v and (not int_v or int_v == 0):
            ic = 9999  # no interest expense
    if not ic:
        _ic_yf = ydata.get("interestCoverage") or ydata.get("ebitToInterest")
        if _ic_yf: ic = float(_ic_yf)

    pfcf = gf(km,"pfcfRatioTTM","priceToFreeCashFlowsRatioTTM","priceToFreeCashFlowRatioTTM")
    # yfinance direct field first
    if not pfcf:
        _pfcf_yf = ydata.get("priceToFreeCashflow") or ydata.get("priceToFreeCashFlows")
        if _pfcf_yf and float(_pfcf_yf) > 0:
            pfcf = float(_pfcf_yf)
    # Calculate from FCF per share × price
    if not pfcf:
        fcf_ps = gf(km,"freeCashFlowPerShareTTM")
        px_raw = gf(prof,"price") or ydata.get("currentPrice")
        if fcf_ps and px_raw and float(fcf_ps) > 0:
            pfcf = float(px_raw) / float(fcf_ps)
    # Calculate from market cap / total FCF
    if not pfcf:
        mc_yf  = ydata.get("marketCap")
        fcf_yf = ydata.get("freeCashflow")
        if mc_yf and fcf_yf and float(fcf_yf) > 0:
            pfcf = float(mc_yf) / float(fcf_yf)
    # Financial sector companies don't have meaningful FCF — mark as N/A
    sector_val = prof.get("sector","")
    if not pfcf and sector_val in ("Financial Services","Financials","Banking","Insurance"):
        pfcf = -9999  # sentinel for "N/A for financials"

    pb     = (gf(rat,"priceToBookRatioTTM")
              or gf(km,"pbRatioTTM")
              or ydata.get("priceToBook"))

    # PEG: last resort — calculate from P/E / earnings growth
    if not peg and pe and eps_growth and eps_growth > 0:
        try: peg = float(pe) / float(eps_growth)
        except: pass

    # Revenue/EPS growth: compute from income statement, fallback yfinance
    if not rev_growth:
        rev_growth = yf_pct("revenueGrowth")
    if not eps_growth:
        eps_growth = yf_pct("earningsGrowth")

    mc     = gf(prof,"mktCap") or gf(km,"marketCapTTM") or ydata.get("marketCap")
    price  = gf(prof,"price") or ydata.get("currentPrice") or ydata.get("regularMarketPrice")
    ceo    = prof.get("ceo","") or ""
    emp    = prof.get("fullTimeEmployees") or ydata.get("fullTimeEmployees")
    desc   = prof.get("description","") or ydata.get("longBusinessSummary","")
    website= prof.get("website","") or ydata.get("website","")

    # Next earnings date
    next_earn = fetch_next_earnings(sym)
    next_earn_date = next_earn.get("date") or next_earn.get("reportDate") or ""

    cik = str(prof.get("cik") or prof.get("CIK") or "").strip()

    return {
        "symbol":sym, "name":prof.get("companyName",sym),
        "sector":prof.get("sector","—"), "industry":prof.get("industry","—"),
        "mkt_cap":fm(mc), "mkt_cap_raw":mc, "price":f"${price:.2f}" if price else "—",
        "ceo":ceo, "employees":emp, "website":website,
        "description":desc,
        "cik": cik,
        "next_earnings_date": next_earn_date,
        "next_earnings_eps_est": next_earn.get("epsEstimated"),
        "next_earnings_time": next_earn.get("time",""),
        # scoring fields
        "rev_growth":rev_growth, "rev_cagr":rev_cagr, "roic":roic,
        "gross_margin":gm, "op_margin":op_m, "net_margin":net_m,
        "roe":roe, "earnings_yield":ey, "fcf_yield":fcfy,
        "peg":peg, "pe":pe, "ev_ebitda":eveb, "debt_equity":de,
        "current_ratio":cr, "int_coverage":ic,
        "roa":roa, "eps_growth":eps_growth, "pfcf":pfcf, "pb":pb,
    }

def _yf_stmt_to_fmp(df, period_label):
    """Convert a yfinance statement DataFrame to FMP-style list of dicts."""
    if df is None or (hasattr(df,"empty") and df.empty): return []
    rows = []
    for col in df.columns:
        try:
            row = {"date": str(col)[:10], "period": period_label, "symbol": ""}
            for idx in df.index:
                key = str(idx).replace(" ","").replace("/","").replace("&","And")
                try: row[key] = float(df.loc[idx, col]) if df.loc[idx, col] is not None else None
                except: row[key] = None
            rows.append(row)
        except: continue
    return rows

def fetch_dd_extras(sym):
    """Fetch executive data and financial statements for DD page.
    FMP is primary; yfinance is fallback for every statement."""
    execs_raw = fmp("key-executives", {"symbol":sym})
    execs = execs_raw if isinstance(execs_raw,list) else []

    # Full company description (the cached universe row only keeps a 200-char snippet,
    # which cuts off mid-word). Pull the complete text here for the overview.
    _prof = first(fmp("profile", {"symbol": sym}))
    full_description = (_prof.get("description") or "").strip()

    def get_stmt(endpoint, period, limit):
        raw = fmp(endpoint, {"symbol":sym,"period":period,"limit":limit})
        return raw if isinstance(raw,list) else []

    # FMP statements
    inc_a  = get_stmt("income-statement",        "annual",  4)
    inc_q  = get_stmt("income-statement",        "quarter", 4)
    bal_a  = get_stmt("balance-sheet-statement", "annual",  4)
    bal_q  = get_stmt("balance-sheet-statement", "quarter", 4)
    cf_a   = get_stmt("cash-flow-statement",     "annual",  4)
    cf_q   = get_stmt("cash-flow-statement",     "quarter", 4)

    # yfinance fallback for any statement that came back empty
    if YF and (not inc_a or not bal_a or not cf_a):
        try:
            t = yf.Ticker(sym)
            if not inc_a:
                try: inc_a = _yf_stmt_to_fmp(t.income_stmt,            "annual")
                except: pass
            if not inc_q:
                try: inc_q = _yf_stmt_to_fmp(t.quarterly_income_stmt,  "quarter")
                except: pass
            if not bal_a:
                try: bal_a = _yf_stmt_to_fmp(t.balance_sheet,          "annual")
                except: pass
            if not bal_q:
                try: bal_q = _yf_stmt_to_fmp(t.quarterly_balance_sheet,"quarter")
                except: pass
            if not cf_a:
                try: cf_a = _yf_stmt_to_fmp(t.cashflow,                "annual")
                except: pass
            if not cf_q:
                try: cf_q = _yf_stmt_to_fmp(t.quarterly_cashflow,      "quarter")
                except: pass
        except: pass

    earnings_hist = fetch_earnings_history(sym, limit=8)

    return {
        "executives":      execs,
        "description":     full_description,
        "income_annual":   inc_a,
        "income_quarter":  inc_q,
        "balance_annual":  bal_a,
        "balance_quarter": bal_q,
        "cashflow_annual": cf_a,
        "cashflow_quarter":cf_q,
        "earnings_history":earnings_hist,
    }

# ─────────────────────────────────────────────────────────────
# NEWS  — FMP stock news + press releases
# ─────────────────────────────────────────────────────────────
def fetch_news(sym, limit=8):
    """Recent company news + official press releases from FMP, merged and
    sorted newest-first, de-duplicated by URL."""
    items = []
    raw = fmp("news/stock", {"symbols": sym, "limit": limit})
    if isinstance(raw, list):
        items += raw
    pr = fmp("news/press-releases", {"symbols": sym, "limit": 4})
    if isinstance(pr, list):
        for p in pr:
            p["_is_pr"] = True
        items += pr
    seen, out = set(), []
    for it in sorted(items, key=lambda x: x.get("publishedDate", ""), reverse=True):
        u = it.get("url", "")
        if not u or u in seen:
            continue
        seen.add(u)
        out.append(it)
    return out[:limit]

def news_ago(dt_str):
    """Human 'time ago' from an FMP 'YYYY-MM-DD HH:MM:SS' timestamp."""
    from datetime import datetime as _dt
    try:
        d    = _dt.strptime(str(dt_str)[:19], "%Y-%m-%d %H:%M:%S")
        secs = (_dt.now() - d).total_seconds()
        if secs < 0:      return d.strftime("%b %d, %Y")
        if secs < 3600:   return f"{int(secs//60)}m ago"
        if secs < 86400:  return f"{int(secs//3600)}h ago"
        days = int(secs // 86400)
        return f"{days}d ago" if days < 30 else d.strftime("%b %d, %Y")
    except Exception:
        return str(dt_str)[:10]

# ─────────────────────────────────────────────────────────────
# EARNINGS HELPERS
# ─────────────────────────────────────────────────────────────
def fetch_earnings_history(sym, limit=8):
    """Historical earnings surprises from yfinance."""
    if not YF:
        return []
    try:
        t = yf.Ticker(sym)
        rows = []

        def sane_eps(v):
            """Return float if it looks like a per-share EPS value, else None."""
            try:
                f = float(v)
                return f if (-500 < f < 5000) and str(v) not in ("nan","None","") else None
            except: return None

        # Primary: earnings_dates — most reliable source in current yfinance
        try:
            ed = t.earnings_dates
            if ed is not None and not ed.empty:
                count = 0
                for idx, row in ed.iterrows():
                    if count >= limit: break
                    # Try every possible column name variation
                    actual   = None
                    estimate = None
                    for col in ed.columns:
                        cl = col.lower().replace(" ","").replace("_","")
                        v  = row[col]
                        if cl in ("reportedeps","epsactual","actualeps","actual"):
                            candidate = sane_eps(v)
                            if candidate is not None: actual = candidate
                        if cl in ("epsestimate","estimatedeps","estimate","epsestimated"):
                            candidate = sane_eps(v)
                            if candidate is not None: estimate = candidate
                    if actual is None and estimate is None: continue
                    rows.append({
                        "date": str(idx)[:10],
                        "actualEarningResult": actual,
                        "estimatedEarning":    estimate,
                    })
                    count += 1
                if rows:
                    return rows
        except: pass

        # Fallback: earnings_history
        try:
            hist = t.earnings_history
            if hist is not None and not hist.empty and len(hist.columns) > 0:
                for idx, row in hist.head(limit).iterrows():
                    actual = estimate = None
                    for col in hist.columns:
                        cl = col.lower().replace(" ","").replace("_","")
                        v  = row[col]
                        if cl in ("reportedeps","epsactual","actualeps","actual"):
                            candidate = sane_eps(v)
                            if candidate is not None: actual = candidate
                        if cl in ("epsestimate","estimatedeps","estimate","epsestimated"):
                            candidate = sane_eps(v)
                            if candidate is not None: estimate = candidate
                    if actual is None and estimate is None: continue
                    rows.append({
                        "date": str(idx)[:10],
                        "actualEarningResult": actual,
                        "estimatedEarning":    estimate,
                    })
                if rows:
                    return rows
        except: pass

        return []
    except:
        return []

def fetch_next_earnings(sym):
    """Next upcoming earnings date from yfinance."""
    if not YF:
        return {}
    from datetime import date as _date, datetime as _datetime
    today = _date.today()
    try:
        t    = yf.Ticker(sym)
        result = {}
        earn_date = None

        # Try 1: calendar dict
        try:
            cal = t.calendar
            if isinstance(cal, dict) and cal:
                ed = cal.get("Earnings Date") or cal.get("earningsDate")
                if ed:
                    if hasattr(ed, '__iter__') and not isinstance(ed, str):
                        ed = list(ed)
                        if ed: earn_date = str(ed[0])[:10]
                    else:
                        earn_date = str(ed)[:10]
                eps_est = cal.get("EPS Estimate") or cal.get("epsEstimate")
                if eps_est is not None:
                    try: result["epsEstimated"] = float(eps_est)
                    except: pass
        except: pass

        # Try 2: earnings_dates DataFrame — find first future date
        if not earn_date:
            try:
                ed_df = t.earnings_dates
                if ed_df is not None and not ed_df.empty:
                    for idx in ed_df.index:
                        d = str(idx)[:10]
                        try:
                            if _date.fromisoformat(d) >= today:
                                earn_date = d
                                # EPS estimate from this row
                                row = ed_df.loc[idx]
                                eps = row.get("EPS Estimate") if hasattr(row,"get") else None
                                if eps is not None and str(eps) != "nan":
                                    result["epsEstimated"] = float(eps)
                                break
                        except: continue
            except: pass

        # Try 3: info timestamp
        if not earn_date:
            try:
                info = t.info or {}
                ts = info.get("earningsTimestamp") or info.get("earningsTimestampStart")
                if ts:
                    earn_date = _datetime.utcfromtimestamp(int(ts)).strftime("%Y-%m-%d")
                if not result.get("epsEstimated"):
                    eps = info.get("epsForward")
                    if eps: result["epsEstimated"] = float(eps)
            except: pass

        if earn_date:
            result["date"] = earn_date
        result["time"] = ""
        return result
    except:
        return {}

# ─────────────────────────────────────────────────────────────
# LEGAL & LITIGATION  — SEC EDGAR + Claude AI summarization
# ─────────────────────────────────────────────────────────────
def fetch_legal_data(sym, cik):
    """
    Fetches raw document text from SEC EDGAR.
    Sends beginning + end of 10-K to Claude so it can find
    both Item 3 disclosures AND referenced footnotes.
    """
    import html as _html

    headers = {"User-Agent": "StockScreener research@stockscreener.com"}
    result = {"tenk_text": "", "eightk_items": [], "cik_used": "", "error": ""}

    # Resolve CIK
    cik_padded = ""
    if cik:
        try: cik_padded = str(int(cik)).zfill(10)
        except: pass
    if not cik_padded:
        try:
            r = requests.get("https://www.sec.gov/files/company_tickers.json",
                             headers=headers, timeout=10)
            if r.status_code == 200:
                for entry in r.json().values():
                    if entry.get("ticker","").upper() == sym.upper():
                        cik_padded = str(entry["cik_str"]).zfill(10)
                        break
        except: pass
    if not cik_padded:
        result["error"] = "Could not resolve CIK."
        return result

    result["cik_used"] = cik_padded
    cik_int = int(cik_padded)

    # Get filing list
    try:
        sub_r = requests.get(
            f"https://data.sec.gov/submissions/CIK{cik_padded}.json",
            headers=headers, timeout=15)
        if sub_r.status_code != 200:
            result["error"] = f"EDGAR {sub_r.status_code}"
            return result
        subs = sub_r.json()
    except Exception as e:
        result["error"] = str(e)
        return result

    filings = subs.get("filings", {}).get("recent", {})
    forms   = filings.get("form", [])
    accnums = filings.get("accessionNumber", [])
    dates   = filings.get("filingDate", [])
    docs    = filings.get("primaryDocument", [])

    def strip_ix(href):
        return href.split("ix?doc=")[-1] if "ix?doc=" in href else href

    def clean_html_text(html):
        text = re.sub(r'<style[^>]*>.*?</style>', ' ', html, flags=re.DOTALL|re.IGNORECASE)
        text = re.sub(r'<script[^>]*>.*?</script>', ' ', text, flags=re.DOTALL|re.IGNORECASE)
        text = re.sub(r'<[^>]+>', ' ', text)
        text = _html.unescape(text)
        text = re.sub(r'[ \t]+', ' ', text)
        text = re.sub(r'\n{3,}', '\n', text)
        return text.strip()

    def is_exhibit_name(hl):
        """True for exhibit / certification / XBRL-sidecar filenames we must skip.
        The regex catches ex-99, exhibit10, AND the hyphen-less 'xex322' style
        (e.g. nvda-2026xex322.htm) that the old substring filter missed."""
        return ("exhibit" in hl
                or bool(re.search(r'(^|[^a-z])x?ex[-_]?\d', hl))
                or any(x in hl for x in ["xsd", "_cal", "_def", "_lab", "_pre"]))

    def fetch_doc_text(acc_raw, primary_doc, cik_int, max_chars=400000):
        acc_nodash = acc_raw.replace("-", "")
        base = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_nodash}"

        # EDGAR's declared primaryDocument IS the main filing body (the real
        # 10-K/10-Q/8-K), so try it FIRST. This is the authoritative source and
        # stops us from mistakenly returning a one-page certification exhibit.
        doc_urls = [(100, f"{base}/{primary_doc}")]

        # Then queue the other real documents from the filing index as fallbacks,
        # excluding exhibits/certifications and XBRL sidecar files.
        try:
            ri = requests.get(f"{base}/{acc_raw}-index.htm", headers=headers, timeout=12)
            if ri.status_code == 200:
                cands = []
                for href in re.findall(r'href="([^"]+)"', ri.text, re.IGNORECASE):
                    href = strip_ix(href)
                    hl = href.lower()
                    if not (hl.endswith(".htm") or hl.endswith(".html")): continue
                    if is_exhibit_name(hl) or "-index" in hl: continue
                    if primary_doc.lower() in hl: continue  # already queued first
                    score = ((4 if sym.lower() in hl else 0)
                             + (3 if any(t in hl for t in ["10-k", "10q", "10-q", "8-k"]) else 0))
                    full = (f"https://www.sec.gov{href}" if href.startswith("/")
                            else f"{base}/{href}")
                    cands.append((score, full))
                cands.sort(reverse=True)
                doc_urls += cands
        except: pass

        # Fetch in priority order, keeping the LONGEST body we find (the real
        # 10-K runs to hundreds of KB; a certification exhibit is a few KB). For
        # the full-document pass (max_chars=None) return as soon as we hit a
        # clearly-substantial body; for the lighter 8-K pass, the first good
        # body (primary) is enough — keeps the 8-K scan fast.
        best = ""
        for _, url in doc_urls[:6]:
            try:
                url = strip_ix(url)
                r = requests.get(url, headers=headers, timeout=30)
                if r.status_code != 200: continue
                if "ixvFrame" in r.text or len(r.text) < 1000: continue
                text = clean_html_text(r.text)
                if len(text) > len(best): best = text
                if len(text) > 20000: break                       # main filing body — done
                if max_chars is not None and len(text) > 500: break  # 8-K: first good body
            except: continue
        if best and len(best) > 500:
            return best if max_chars is None else best[:max_chars]
        return ""

    def extract_legal_sections(text, budget=95000):
        """Pull the windows of a 10-K that hold the REAL legal disclosures —
        Item 3 Legal Proceedings, the Legal Matters / Litigation subsection, and
        the Commitments & Contingencies footnote (which routinely sits past the
        300K-char mark). Structure-driven, not density-driven: the old approach
        ranked purely by keyword density and always got swamped by the Risk
        Factors section (wall-to-wall generic legal words, but hypothetical — not
        actual disclosed matters). This version explicitly targets the
        authoritative sections and penalizes the Risk Factors region."""
        cl = text.lower(); n = len(text)
        legal_terms = ["proceeding", "litigation", "lawsuit", "claim", "plaintiff",
                       "defendant", "settlement", "alleg", "court", "regulatory",
                       "investigation", "damages", "judgment", "arbitration",
                       "complaint", "subpoena", "penalt", "consent order",
                       "antitrust", "class action"]

        # Locate the Risk Factors region (Item 1A → Item 1B / Item 2). Content
        # here is generic boilerplate ("we may face litigation…"), so we push it
        # down rather than let it crowd out the actual disclosures.
        ia = cl.find("item 1a"); ib = cl.find("item 1b")
        if ib < 0 or ib < ia:
            ib = cl.find("item 2", ia + 10) if ia >= 0 else -1
        rf_lo, rf_hi = (ia, ib) if (ia >= 0 and ib > ia) else (-1, -1)
        def in_risk_factors(p):
            return rf_lo >= 0 and rf_lo <= p < rf_hi

        windows = []
        seen_buckets = set()
        def add_window(pos, boost):
            bucket = pos // 5000
            if bucket in seen_buckets: return
            seen_buckets.add(bucket)
            w  = text[max(0, pos - 900): min(n, pos + 12000)]
            wl = w.lower()
            legal    = sum(wl.count(t) for t in legal_terms)
            # Reward hallmarks of a REAL disclosed case: dollar amounts, case
            # captions ("X v. Y"), named parties, settlements.
            specific = (wl.count("$") + wl.count(" v. ")
                        + 2 * sum(wl.count(k) for k in
                                  ["plaintiff", "defendant", "settlement",
                                   "lawsuit", "damages", "alleg", "court"]))
            # Penalize table-of-contents rows ("item 3 legal proceedings 44").
            toc = len(re.findall(r'(item|note)\s+\d+\s+\d', wl))
            score = legal + specific + boost - toc * 3 - (45 if in_risk_factors(pos) else 0)
            windows.append((score, pos, w))

        # Highest-value structural headings first, then the contingencies note.
        for anchor in ["legal proceedings", "legal matters"]:
            idx = 0
            while True:
                pos = cl.find(anchor, idx)
                if pos < 0: break
                idx = pos + len(anchor); add_window(pos, boost=30)
        for anchor in ["commitments and contingencies", "contingencies", "litigation"]:
            idx = 0
            while True:
                pos = cl.find(anchor, idx)
                if pos < 0: break
                idx = pos + len(anchor); add_window(pos, boost=8)

        windows = [w for w in windows if w[0] > 0]
        windows.sort(key=lambda x: x[0], reverse=True)
        chunks, used = [], 0
        for score, pos, w in windows:
            if used + len(w) > budget:
                w = w[:budget - used]
                if len(w) > 500: chunks.append((pos, w))
                break
            chunks.append((pos, w)); used += len(w)

        if not chunks:
            # Nothing scored as real legal content — give Claude head + tail.
            return text[:20000] + "\n\n[...middle omitted...]\n\n" + text[-20000:]

        # Emit in document order so the narrative reads naturally.
        chunks.sort(key=lambda x: x[0])
        return "\n\n=== LEGAL SECTION ===\n".join(c[1] for c in chunks)

    # Fetch 10-K — search the FULL document (max_chars=None). The real Item 3
    # text and the Legal Proceedings note routinely sit past the 400K mark, so
    # pre-truncating would discard exactly the content we need.
    tenk_idx = next((i for i, f in enumerate(forms) if f == "10-K"), None)
    if tenk_idx is not None:
        text = fetch_doc_text(accnums[tenk_idx], docs[tenk_idx], cik_int, max_chars=None)
        if text:
            result["tenk_text"] = extract_legal_sections(text)

    # NOTE: the old 8-K keyword scan (fetching up to 40 filings + a second AI
    # call) was removed — low signal, high latency. The DD page now links
    # straight to recent 8-K filings on EDGAR instead. See show_dd_page().

    return result

def summarize_legal_with_claude(sym, tenk_text, eightk_items):
    """Use Claude API to summarize legal data into clean readable sections."""
    # Sentinels per section: "" = no source filing to summarize,
    # None = a source existed but the API call failed (key/network),
    # a string = a real summary. The UI shows different messages for each.
    if not tenk_text and not eightk_items:
        return "", ""

    tenk_summary   = ""
    eightk_summary = ""

    if tenk_text:
        tenk_summary = None   # attempted; stays None unless the call succeeds
        try:
            prompt = (
                f"You are a financial analyst reviewing SEC filings for {sym}. "
                f"The following text is extracted from their most recent 10-K annual report — it targets Item 3 Legal Proceedings, the Legal Matters / Litigation subsection, and the Commitments & Contingencies footnote. "
                f"Your task: summarize the company's ACTUAL, SPECIFIC legal matters — real pending or settled cases, government investigations, regulatory actions, and material contingencies. Look in: "
                f"(1) Item 3 Legal Proceedings directly, "
                f"(2) if Item 3 says 'see Note X' or 'see Contingencies', the referenced footnote, "
                f"(3) the commitments and contingencies footnote. "
                f"CRITICAL: Only report matters that are actually disclosed as real/pending/threatened (named cases, parties, dollar amounts, investigations, settlements). Do NOT summarize generic hypothetical 'we may face litigation' language from Risk Factors — that is boilerplate, not a disclosed matter. If the text genuinely contains only generic risk-factor language and no specific disclosed proceedings, say exactly that. "
                f"Summarize in 5-8 sentences covering: main disputes, who is involved, dollar amounts at stake, jurisdictions, and management's view on outcomes. "
                f"Write in plain prose only — no markdown headers or bullet points.\n\n"
                f"FILING TEXT:\n{tenk_text[:100000]}"
            )
            _r = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-sonnet-4-6",
                    "max_tokens": 800,
                    "messages": [{"role": "user", "content": prompt}]
                },
                timeout=40
            )
            if _r.status_code == 200:
                tenk_summary = _r.json()["content"][0]["text"].strip()
        except Exception as e:
            pass

    if eightk_items:
        eightk_summary = None   # attempted; stays None unless the call succeeds
        try:
            items_text = "\n\n".join(
                [f"Date: {x['date']}\n{x['snippet']}" for x in eightk_items]
            )
            prompt2 = (
                f"You are a financial analyst reviewing recent 8-K SEC filings for {sym} "
                f"that contain legal keywords. Summarize any new legal developments "
                f"(new lawsuits, settlements, investigations, judgments) in 5-7 sentences. For each: what happened, when, parties involved, dollar amounts if disclosed, and current status. "
                f"Write in plain prose only — no markdown headers or bullet points.\n\n"
                f"FILINGS:\n{items_text[:3000]}"
            )
            _r2 = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-sonnet-4-6",
                    "max_tokens": 600,
                    "messages": [{"role": "user", "content": prompt2}]
                },
                timeout=20
            )
            if _r2.status_code == 200:
                eightk_summary = _r2.json()["content"][0]["text"].strip()
        except Exception as e:
            pass

    return tenk_summary, eightk_summary

# ─────────────────────────────────────────────────────────────
# RANKING ENGINE
# ─────────────────────────────────────────────────────────────
def rank_universe(data, selected):
    if not selected or not data: return data
    df = pd.DataFrame(data); score_cols = []
    for m in selected:
        defn = METRIC_DEFS[m]; field = defn["field"]; higher = defn["higher"]
        if field not in df.columns: continue
        col = pd.to_numeric(df[field], errors="coerce")
        if col.notna().sum() == 0: continue
        ranked = col.rank(method="min", na_option="bottom",
                          pct=True, ascending=higher) * 100
        sc = f"__s_{field}"; df[sc] = ranked; score_cols.append(sc)
    df["composite_score"] = df[score_cols].mean(axis=1).round(1) if score_cols else 0
    return df.drop(columns=score_cols,errors="ignore").sort_values(
        "composite_score",ascending=False).to_dict("records")

# ─────────────────────────────────────────────────────────────
# MAIN PAGE CARD  (hero tiles only)
# ─────────────────────────────────────────────────────────────
def main_card_html(r, rank, selected, all_data, sec_ranks=None, total_count=0):
    sc = r.get("composite_score",0)
    rank_bg  = "#0d3a1a"
    rank_fg  = C["green"]

    tiles = ""
    for m in selected:
        defn=METRIC_DEFS[m]; field=defn["field"]; v=r.get(field); higher=defn["higher"]
        if v is None: continue
        vs = fp(v) if defn["fmt"]=="pct" else fx(v)
        vals=[d.get(field) for d in all_data if d.get(field) is not None]
        if vals:
            ri=sorted(vals,reverse=higher).index(min(vals,key=lambda x:abs(x-v)))+1
            pr=max(0,min(100,(1-(ri-1)/len(vals))*100 if higher else (ri/len(vals))*100))
        else: pr=sc
        tc=C["green"] if pr>=75 else C["amber"] if pr>=40 else C["red"]
        tbg="#162520" if pr>=75 else "#221e14" if pr>=40 else "#221414"
        rl=f"Top {100-pr:.0f}%" if pr>50 else f"Bottom {pr:.0f}%"
        tip_safe=(defn["tip"][:80]+"...").replace("'","&#39;")
        tiles+=(f"<div title='{tip_safe}' style='background:{tbg};border:1px solid {tc}44;"
                f"border-radius:8px;padding:12px 14px;flex:1;min-width:100px;cursor:default;'>"
                f"<div style='font-size:9px;font-weight:600;text-transform:uppercase;"
                f"letter-spacing:1px;color:{C['dim']};margin-bottom:5px;'>{m}</div>"
                f"<div style='font-family:IBM Plex Mono,monospace;font-size:20px;"
                f"font-weight:600;color:{tc};line-height:1;'>{vs}</div>"
                f"<div style='display:flex;align-items:center;gap:5px;margin-top:7px;'>"
                f"<div style='flex:1;background:{C['bg3']};border-radius:2px;height:3px;overflow:hidden;'>"
                f"<div style='width:{pr:.0f}%;height:100%;background:{tc};'></div></div>"
                f"<span style='font-size:9px;color:{tc};font-family:IBM Plex Mono,monospace;"
                f"white-space:nowrap;'>{rl}</span></div></div>")

    # Build secondary rankings HTML
    sec_html = ""
    if sec_ranks:
        sec_rows = ""
        for sec_name, rank_field in sec_ranks.items():
            rank_val   = r.get(rank_field)
            metric_val = r.get(METRIC_DEFS[sec_name]["field"])
            defn_s     = METRIC_DEFS[sec_name]
            val_str    = fp(metric_val) if defn_s["fmt"]=="pct" else fx(metric_val)
            rank_str   = f"#{int(rank_val)} of {total_count}" if rank_val else "—"
            bdr = C["border"]; clr_dim = C["dim"]; clr_amb = C["amber"]
            sec_rows += (
                f"<div style='display:flex;justify-content:space-between;"
                f"align-items:center;padding:5px 0;border-top:1px solid {bdr};'>"
                f"<span style='font-size:11px;color:{clr_dim};'>{sec_name}</span>"
                f"<div style='text-align:right;'>"
                f"<span style='font-family:IBM Plex Mono,monospace;font-size:11px;"
                f"font-weight:600;color:{clr_amb};'>{rank_str}</span>"
                f"<span style='font-size:10px;color:{clr_dim};margin-left:6px;'>{val_str}</span>"
                f"</div></div>"
            )
        clr_muted = C["muted"]
        sec_html = (
            f"<div style='margin-top:10px;padding-top:8px;border-top:1px solid #388bfd33;'>"
            f"<div style='font-size:9px;font-weight:600;text-transform:uppercase;"
            f"letter-spacing:1px;color:{clr_muted};margin-bottom:6px;'>"
            f"📊 Secondary Rankings</div>"
            f"{sec_rows}</div>"
        )

    bg2=C["bg2"]; brd="rgba(255,255,255,0.1)"; muted=C["text"]; txt=C["text"]
    dim=C["text"]; bbg=C["blue_bg"]

    return (
        f"<div style='background:{bg2};border:1.5px solid {brd};"
        f"border-radius:12px;padding:18px 20px;margin-bottom:4px;'>"
        f"<div style='display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:14px;'>"
        f"<div>"
        f"<div style='font-family:IBM Plex Mono,monospace;font-size:20px;font-weight:600;color:{txt};'>{r['symbol']}</div>"
        f"<div style='font-size:12px;color:{dim};margin-top:2px;'>{r['name']}</div>"
        f"<div style='font-size:11px;color:{muted};'>{r['sector']}</div>"
        f"</div>"
        f"<div style='text-align:right;'>"
        f"<span style='padding:4px 10px;border-radius:10px;background:{rank_bg};color:{rank_fg};"
        f"font-family:IBM Plex Mono,monospace;font-size:12px;font-weight:600;'>Rank {rank}</span>"
        f"<div style='font-size:12px;color:{dim};margin-top:5px;'>{r['mkt_cap']}</div>"
        f"<div style='font-size:11px;color:{muted};'>{r['price']}</div>"
        f"</div></div>"
        f"<div style='border:1px solid #388bfd33;background:{bbg};border-radius:8px;padding:12px 14px;'>"
        f"<div style='font-size:9px;font-weight:600;text-transform:uppercase;letter-spacing:1px;"
        f"color:#388bfd;margin-bottom:10px;'>🎯 Why It Made The List</div>"
        f"<div style='display:flex;flex-wrap:wrap;gap:7px;'>{tiles}</div>"
        + sec_html
        + f"</div></div>"
    )

# ─────────────────────────────────────────────────────────────
# PROJECTION ENGINE  — forward revenue / EPS modeling
# ─────────────────────────────────────────────────────────────
def extract_projection_anchors(income_annual):
    """Read the 'starting line' for a projection from the company's own
    historical annual income statements (FMP returns them most-recent-first).

    These are facts, not guesses — they anchor every scenario to reality.
    Returns a dict, or None if there's no usable base year:
        base_revenue – latest fiscal-year revenue (absolute $)
        base_eps     – latest diluted EPS
        shares       – implied diluted share count (reported, else NetIncome/EPS)
        net_margin   – latest net margin as a % (e.g. 25.3)
        rev_cagr     – historical revenue CAGR % across the available years
        hist         – [{year, revenue, net_income, eps}] ordered oldest→newest
    """
    if not income_annual:
        return None

    rows = list(income_annual)  # newest-first as FMP delivers it

    # Robust field readers — FMP and the yfinance fallback name things differently.
    def rev(r): return gf(r, "revenue", "totalRevenue", "TotalRevenue", "Revenue")
    def ni(r):  return gf(r, "netIncome", "NetIncome", "netIncomeLoss")
    def eps(r): return gf(r, "epsdiluted", "eps", "DilutedEPS", "dilutedEPS")
    def yr(r):  return (r.get("date") or "")[:4]

    base     = rows[0]
    base_rev = rev(base)
    base_ni  = ni(base)
    base_eps = eps(base)
    if not base_rev:
        return None  # no revenue = nothing to project

    # Diluted shares: prefer the reported count, else back it out of NI / EPS.
    shares = gf(base, "weightedAverageShsOutDil", "weightedAverageShsOut")
    if not shares and base_ni and base_eps:
        shares = base_ni / base_eps

    # Net margin %: prefer the reported ratio, else compute NI / Revenue.
    net_margin = None
    nm_ratio = gf(base, "netIncomeRatio")
    if nm_ratio is not None:
        net_margin = as_pct(nm_ratio)
    elif base_ni and base_rev:
        net_margin = base_ni / base_rev * 100

    # Historical revenue CAGR across however many years we have.
    oldest   = rows[-1]
    old_rev  = rev(oldest)
    n_years  = len(rows) - 1
    rev_cagr = None
    if old_rev and old_rev > 0 and base_rev > 0 and n_years >= 1:
        rev_cagr = ((base_rev / old_rev) ** (1 / n_years) - 1) * 100

    hist = [{"year": yr(r), "revenue": rev(r), "net_income": ni(r), "eps": eps(r)}
            for r in reversed(rows)]  # oldest → newest, for charting

    return {
        "base_revenue": base_rev,
        "base_eps":     base_eps,
        "shares":       shares,
        "net_margin":   net_margin,
        "rev_cagr":     rev_cagr,
        "hist":         hist,
    }


def project_financials(base_revenue, shares, growth_pct, net_margin_pct, years=5):
    """The core projection math — pure arithmetic, no fetching, no Streamlit.

    Walks revenue forward `years` periods at `growth_pct` per year, turns each
    year's revenue into net income via `net_margin_pct`, then into EPS by
    dividing by the (held-constant) diluted share count:

        revenue_n    = base_revenue * (1 + g) ** n
        net_income_n = revenue_n * margin
        eps_n        = net_income_n / shares

    Returns [{year_offset, revenue, net_income, eps}] for n = 1..years.
    """
    g = growth_pct / 100.0
    m = net_margin_pct / 100.0
    out = []
    for n in range(1, years + 1):
        rev_n = base_revenue * ((1 + g) ** n)
        ni_n  = rev_n * m
        eps_n = (ni_n / shares) if shares else None
        out.append({"year_offset": n, "revenue": rev_n,
                    "net_income": ni_n, "eps": eps_n})
    return out


def _clamp(v, lo, hi, default):
    """Keep an assumption inside sane bounds so a bad value can't break the
    sliders (e.g. a hallucinated 900% growth, or a None)."""
    try:
        f = float(v)
        if f != f:  # NaN
            return default
        return max(lo, min(hi, f))
    except (TypeError, ValueError):
        return default


def historical_default_assumptions(anchors):
    """Instant, API-free starting assumptions derived from the company's own
    numbers. Base = its historical revenue CAGR & current net margin; bear and
    bull fan out ±5 growth points / ±2 margin points around base.

    Returns {bear/base/bull: {growth, margin, rationale}}. This is also the
    fallback whenever the Claude call can't run.
    """
    base_g = _clamp(anchors.get("rev_cagr"), -50, 60, 8.0)
    base_m = _clamp(anchors.get("net_margin"), -20, 60, 10.0)
    cagr_txt = (f"{base_g:.1f}% historical revenue CAGR"
                if anchors.get("rev_cagr") is not None
                else "an 8% default (no usable history)")
    marg_txt = (f"{base_m:.1f}% current net margin"
                if anchors.get("net_margin") is not None
                else "a 10% default margin")
    return {
        "bear": {"growth": _clamp(base_g - 5, -50, 60, 3.0),
                 "margin": _clamp(base_m - 2, -20, 60, 8.0),
                 "rationale": f"Growth slows ~5pts below {cagr_txt}; margins compress slightly."},
        "base": {"growth": base_g, "margin": base_m,
                 "rationale": f"Carries {cagr_txt} and {marg_txt} forward."},
        "bull": {"growth": _clamp(base_g + 5, -50, 60, 13.0),
                 "margin": _clamp(base_m + 2, -20, 60, 12.0),
                 "rationale": f"Growth accelerates ~5pts above {cagr_txt}; margins expand slightly."},
    }


def generate_projection_assumptions(sym, anchors):
    """Ask Claude for reasoned bear/base/bull {growth, margin} assumptions,
    seeded by the company's real anchors.

    Returns (assumptions_dict, error_str). On success error_str is "".
    On failure assumptions_dict is None and error_str holds a plain-English
    reason to surface in the UI (out of calls, bad key, network, etc.).
    """
    if not ANTHROPIC_API_KEY:
        return None, "No Anthropic API key configured in .streamlit/secrets.toml."

    hist = anchors.get("hist", [])
    hist_txt = "; ".join(
        f"{h['year']}: rev ${ (h['revenue'] or 0)/1e9:.1f}B, "
        f"EPS ${h['eps']:.2f}" if h.get("eps") is not None else f"{h['year']}: rev ${(h['revenue'] or 0)/1e9:.1f}B"
        for h in hist
    ) or "no multi-year history available"

    cagr = anchors.get("rev_cagr")
    marg = anchors.get("net_margin")
    cagr_line = f"Historical revenue CAGR: {cagr:.1f}%.\n" if cagr is not None else ""
    marg_line = f"Current net margin: {marg:.1f}%.\n" if marg is not None else ""
    prompt = (
        f"You are an equity analyst building a 5-year forward model for {sym}.\n"
        f"Historical annual results (oldest→newest): {hist_txt}.\n"
        + cagr_line
        + marg_line
        + "Propose three scenarios for the NEXT 5 years. For each, give an annual "
        "revenue GROWTH rate (%) and an average net MARGIN (%), plus a one-sentence "
        "rationale grounded in the company's fundamentals and sector.\n"
        "Bear = conservative/downside, Base = most-likely, Bull = optimistic but plausible.\n"
        "Respond with ONLY a JSON object, no other text, in exactly this shape:\n"
        '{"bear":{"growth":<num>,"margin":<num>,"rationale":"<text>"},'
        '"base":{"growth":<num>,"margin":<num>,"rationale":"<text>"},'
        '"bull":{"growth":<num>,"margin":<num>,"rationale":"<text>"}}'
    )

    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-sonnet-4-6",
                "max_tokens": 700,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=40,
        )
    except Exception as e:
        return None, f"Couldn't reach Claude ({e})."

    if r.status_code == 429:
        return None, "Claude is out of calls right now (rate limit / quota reached). Try again shortly."
    if r.status_code == 401:
        return None, "Claude rejected the API key (401). Check ANTHROPIC_API_KEY."
    if r.status_code != 200:
        return None, f"Claude API returned an error (HTTP {r.status_code})."

    # Parse the JSON object out of Claude's reply.
    try:
        text = r.json()["content"][0]["text"].strip()
        m = re.search(r"\{.*\}", text, re.DOTALL)
        raw = json.loads(m.group(0) if m else text)
        out = {}
        for key in ("bear", "base", "bull"):
            sc = raw.get(key, {})
            out[key] = {
                "growth":    _clamp(sc.get("growth"), -50, 60, 8.0),
                "margin":    _clamp(sc.get("margin"), -20, 60, 10.0),
                "rationale": str(sc.get("rationale", ""))[:240],
            }
        return out, ""
    except Exception as e:
        return None, f"Claude replied but the format couldn't be parsed ({e})."


def render_projections_section(sym, extras):
    """The Projections UI: anchors → assumptions → sliders → charts + table.
    Lives inline on the DD page (like the Charts / Legal sections)."""
    red=C["red"]; blue=C["blue"]; green=C["green"]; muted=C["muted"]
    dim=C["dim"]; text=C["text"]; bg2=C["bg2"]; border=C["border"]

    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown(
        f"<div style='font-size:10px;font-weight:600;text-transform:uppercase;"
        f"letter-spacing:1px;color:{muted};margin-bottom:12px;'>🔮 Financial Projections</div>",
        unsafe_allow_html=True)

    # 1 — anchors from the company's real history
    anchors = extract_projection_anchors(extras.get("income_annual", []))
    if not anchors or not anchors.get("base_revenue"):
        st.info("Not enough historical income-statement data to build projections.")
        return
    can_eps = bool(anchors.get("shares"))

    # 2 — seed slider state once per symbol from the instant historical defaults
    seed_key = f"proj_seeded_{sym}"
    SCN = [("bear","🐻 Bear",red), ("base","📊 Base",blue), ("bull","🐂 Bull",green)]
    if not st.session_state.get(seed_key):
        d = historical_default_assumptions(anchors)
        for sc, _, _ in SCN:
            st.session_state[f"proj_g_{sc}_{sym}"] = float(d[sc]["growth"])
            st.session_state[f"proj_m_{sc}_{sym}"] = float(d[sc]["margin"])
        st.session_state[f"proj_rat_{sym}"] = {sc: d[sc]["rationale"] for sc,_,_ in SCN}
        st.session_state[f"proj_src_{sym}"] = "Historical defaults — this company's own CAGR & margin"
        st.session_state[seed_key] = True

    # 3 — Claude button (writes into the slider keys, then reruns)
    cbtn, csrc = st.columns([1, 2])
    with cbtn:
        if st.button("✨ Ask Claude for assumptions", key=f"proj_ai_{sym}", width='stretch'):
            with st.spinner("Claude is modeling bear / base / bull…"):
                a, err = generate_projection_assumptions(sym, anchors)
            if a:
                for sc, _, _ in SCN:
                    st.session_state[f"proj_g_{sc}_{sym}"] = float(a[sc]["growth"])
                    st.session_state[f"proj_m_{sc}_{sym}"] = float(a[sc]["margin"])
                st.session_state[f"proj_rat_{sym}"] = {sc: a[sc]["rationale"] for sc,_,_ in SCN}
                st.session_state[f"proj_src_{sym}"] = "Claude AI"
                st.session_state.pop(f"proj_err_{sym}", None)
                st.rerun()
            else:
                st.session_state[f"proj_err_{sym}"] = err
    with csrc:
        src = st.session_state.get(f"proj_src_{sym}", "")
        cagr_s = f"{anchors['rev_cagr']:.1f}%" if anchors.get("rev_cagr") is not None else "n/a"
        marg_s = f"{anchors['net_margin']:.1f}%" if anchors.get("net_margin") is not None else "n/a"
        st.markdown(
            f"<div style='font-size:11px;color:{dim};padding-top:6px;'>"
            f"Assumptions source: <b style='color:{text};'>{src}</b><br>"
            f"<span style='color:{muted};'>Anchors — base revenue {fm(anchors['base_revenue'])} · "
            f"hist. CAGR {cagr_s} · net margin {marg_s}</span></div>",
            unsafe_allow_html=True)

    if st.session_state.get(f"proj_err_{sym}"):
        st.warning(f"⚠ Claude unavailable — {st.session_state[f'proj_err_{sym}']} "
                   f"Showing the last good assumptions instead.")

    # 4 — horizon + per-scenario sliders
    years = st.slider("Projection horizon (years)", 3, 7, 5, key=f"proj_years_{sym}")
    rationales = st.session_state.get(f"proj_rat_{sym}", {})
    cfg = {}
    scols = st.columns(3)
    for (sc, label, color), col in zip(SCN, scols):
        with col:
            st.markdown(f"<div style='font-size:13px;font-weight:700;color:{color};"
                        f"margin-bottom:2px;'>{label}</div>", unsafe_allow_html=True)
            g = st.slider("Revenue growth %/yr", -50.0, 60.0, step=0.5, key=f"proj_g_{sc}_{sym}")
            m = st.slider("Net margin %", -20.0, 60.0, step=0.5, key=f"proj_m_{sc}_{sym}")
            cfg[sc] = (g, m)
            rat = rationales.get(sc, "")
            if rat:
                st.markdown(f"<div style='font-size:10px;color:{muted};line-height:1.5;"
                            f"margin:-4px 0 8px 0;'>{rat}</div>", unsafe_allow_html=True)

    # 5 — run the engine for each scenario
    projs = {sc: project_financials(anchors["base_revenue"], anchors.get("shares"),
                                    g, m, years) for sc, (g, m) in cfg.items()}

    # Year labels: real years stitched onto the historical timeline
    hist = anchors["hist"]
    hist_years = [h["year"] or f"Y{i}" for i, h in enumerate(hist)]
    base_year  = hist[-1]["year"] if hist else ""
    try:
        by = int(base_year); fut_years = [str(by + k) for k in range(1, years + 1)]
    except (ValueError, TypeError):
        fut_years = [f"+{k}y" for k in range(1, years + 1)]
    x_all = hist_years + fut_years

    # 6 — charts (revenue + EPS), historical actuals leading into 3 scenario lines
    if PLOTLY:
        PDARK = dict(
            paper_bgcolor="#151b27", plot_bgcolor="#0e1015",
            font=dict(color="#ffffff", size=12, family="Inter, sans-serif"),
            margin=dict(l=60, r=20, t=50, b=40), height=360,
            xaxis=dict(gridcolor="rgba(255,255,255,0.06)", zeroline=False, color="#ffffff"),
            yaxis=dict(gridcolor="rgba(255,255,255,0.06)", zeroline=False, color="#ffffff"),
            legend=dict(font=dict(color="#ffffff", size=11), bgcolor="rgba(0,0,0,0)",
                        orientation="h", y=1.12, x=0),
            shapes=[{"type":"rect","xref":"paper","yref":"paper","x0":0,"y0":0,"x1":1,"y1":1,
                     "line":{"color":"rgba(255,255,255,0.25)","width":1}}],
        )
        scen_color = {"bear": red, "base": blue, "bull": green}

        # Revenue ($B)
        hist_rev = [(h["revenue"] or 0)/1e9 for h in hist]
        figr = go.Figure()
        figr.add_trace(go.Scatter(x=x_all, y=hist_rev + [None]*years, name="Actual",
                                  mode="lines+markers", line=dict(color="#c8d8ee", width=2)))
        for sc, label, _ in SCN:
            y = [None]*(len(hist)-1) + [hist_rev[-1]] + [p["revenue"]/1e9 for p in projs[sc]]
            figr.add_trace(go.Scatter(x=x_all, y=y, name=label,
                                      mode="lines+markers",
                                      line=dict(color=scen_color[sc], width=2, dash="dot")))
        figr.update_layout(**PDARK)
        figr.update_yaxes(title_text="Revenue ($B)")
        st.markdown(f"<div style='font-size:14px;font-weight:600;color:#fff;"
                    f"margin-bottom:6px;'>📈 Projected Revenue</div>", unsafe_allow_html=True)
        st.plotly_chart(figr, width='stretch')

        # EPS ($)
        if can_eps:
            hist_eps = [h["eps"] for h in hist]
            anchor_eps = anchors.get("base_eps")
            fige = go.Figure()
            fige.add_trace(go.Scatter(x=x_all, y=hist_eps + [None]*years, name="Actual",
                                      mode="lines+markers", line=dict(color="#c8d8ee", width=2)))
            for sc, label, _ in SCN:
                tail = [p["eps"] for p in projs[sc]]
                if anchor_eps is not None:
                    y = [None]*(len(hist)-1) + [anchor_eps] + tail
                else:
                    y = [None]*len(hist) + tail
                fige.add_trace(go.Scatter(x=x_all, y=y, name=label,
                                          mode="lines+markers",
                                          line=dict(color=scen_color[sc], width=2, dash="dot")))
            fige.update_layout(**PDARK)
            fige.update_yaxes(title_text="Diluted EPS ($)")
            st.markdown(f"<div style='font-size:14px;font-weight:600;color:#fff;"
                        f"margin:10px 0 6px 0;'>💵 Projected EPS</div>", unsafe_allow_html=True)
            st.plotly_chart(fige, width='stretch')
        else:
            st.info("EPS projection unavailable — no diluted share count for this company. "
                    "Revenue projection above still applies.")

    # 7 — headline metrics: horizon-year EPS per scenario
    if can_eps:
        mcols = st.columns(3)
        for (sc, label, color), col in zip(SCN, mcols):
            last = projs[sc][-1]
            with col:
                st.markdown(
                    f"<div style='background:{bg2};border:1px solid {border};"
                    f"border-radius:8px;padding:12px 16px;margin-bottom:8px;'>"
                    f"<div style='font-size:11px;color:{muted};'>{label} · {fut_years[-1]} EPS</div>"
                    f"<div style='font-family:IBM Plex Mono,monospace;font-size:22px;"
                    f"font-weight:600;color:{color};'>${last['eps']:.2f}</div>"
                    f"</div>", unsafe_allow_html=True)

    # 8 — year-by-year table
    tbl = []
    for i, yl in enumerate(fut_years):
        row = {"Year": yl, "Base Revenue": fm(projs["base"][i]["revenue"])}
        if can_eps:
            row["EPS Bear"] = f"${projs['bear'][i]['eps']:.2f}"
            row["EPS Base"] = f"${projs['base'][i]['eps']:.2f}"
            row["EPS Bull"] = f"${projs['bull'][i]['eps']:.2f}"
        tbl.append(row)
    st.dataframe(pd.DataFrame(tbl), width='stretch', hide_index=True)
    st.caption("Projections are model estimates from the assumptions above — not forecasts or advice.")


# ─────────────────────────────────────────────────────────────
# DUE DILIGENCE PAGE
# ─────────────────────────────────────────────────────────────
def fmt_stmt_val(v):
    if v is None or v == 0: return "—"
    try:
        v=float(v)
        if abs(v)>=1e9:  return f"${v/1e9:.2f}B"
        if abs(v)>=1e6:  return f"${v/1e6:.1f}M"
        return f"${v:,.0f}"
    except: return "—"

def build_stmt_df(rows, key_map):
    """key_map: {Display Label: api_field_name}"""
    if not rows: return None
    cols = [r.get("date","")[:7] for r in rows]
    data = {}
    for label, field in key_map.items():
        data[label] = [fmt_stmt_val(r.get(field)) for r in rows]
    df = pd.DataFrame(data, index=cols).T
    df.index.name = "Metric"
    return df

def show_dd_page():
    sym  = st.session_state.selected_symbol
    rrow = next((r for r in (st.session_state.universe_data or []) if r["symbol"]==sym), {})

    # ── BACK BUTTON ────────────────────────────────────────
    _back_col, _pf_dd_col = st.columns([3,1])
    with _back_col:
        if st.button("← Back to Screener Results"):
            st.session_state.page = "screener"; st.rerun()
    with _pf_dd_col:
        _dd_in_pf = pf_in_portfolio(sym)
        _dd_pf_lbl = "✅ In Portfolio" if _dd_in_pf else "Add to Portfolio"
        if st.button(_dd_pf_lbl, width='stretch', key="dd_pf_btn"):
            if _dd_in_pf: pf_remove(sym)
            else: pf_add(sym, st.session_state.universe_data)
            st.rerun()

    st.markdown("<br>", unsafe_allow_html=True)

    # ── FETCH DD EXTRAS ────────────────────────────────────
    with st.spinner(f"Loading full due diligence data for {sym}…"):
        extras = fetch_dd_extras(sym)

    execs = extras["executives"]

    # ── COMPANY HEADER ─────────────────────────────────────
    h1,h2,h3,h4 = st.columns([2,1,1,1])
    with h1:
        st.markdown(
            f"<div style='font-family:IBM Plex Mono,monospace;font-size:28px;"
            f"font-weight:600;color:{C['text']};'>{sym}</div>"
            f"<div style='font-size:15px;color:{C['dim']};margin-top:2px;'>{rrow.get('name','')}</div>"
            f"<div style='font-size:12px;color:{C['muted']};margin-top:3px;'>"
            f"{rrow.get('sector','—')} · {rrow.get('industry','—')}</div>",
            unsafe_allow_html=True)
    rank_pos = next((i+1 for i,d in enumerate(st.session_state.scan_results or []) if d["symbol"]==sym), None)
    for col,lbl,val in [(h2,"Market Cap",rrow.get("mkt_cap","—")),
                        (h3,"Stock Price",rrow.get("price","—")),
                        (h4,"Screener Rank", f"#{rank_pos}" if rank_pos else "—")]:
        with col:
            st.markdown(
                f"<div style='background:{C['bg2']};border:1px solid {C['border']};"
                f"border-radius:8px;padding:12px 14px;'>"
                f"<div style='font-size:10px;color:{C['muted']};text-transform:uppercase;"
                f"letter-spacing:1px;margin-bottom:4px;'>{lbl}</div>"
                f"<div style='font-family:IBM Plex Mono,monospace;font-size:18px;"
                f"font-weight:500;color:{C['blue']};'>{val}</div>"
                f"</div>", unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # ── TECHNICAL ANALYSIS (interactive chart) ────────────
    # TradingView uses a dot for share classes (BRK.B), not a dash (BRK-B).
    tv_sym   = sym.replace("-", ".").upper()
    tv_chart = f"https://www.tradingview.com/chart/?symbol={tv_sym}"
    st.markdown(
        f"<div style='font-size:10px;font-weight:600;text-transform:uppercase;"
        f"letter-spacing:1px;color:{C['muted']};margin-bottom:12px;'>📉 Technical Analysis</div>",
        unsafe_allow_html=True)
    st.markdown(
        f"<div style='background:{C['bg2']};border:1px solid {C['border']};"
        f"border-radius:10px;padding:18px 22px;margin-bottom:20px;'>"
        f"<div style='font-size:13px;color:{C['dim']};line-height:1.7;margin-bottom:14px;'>"
        f"View the full interactive price chart for <b style='color:{C['text']};'>{sym}</b> "
        f"on TradingView — candlesticks, indicators (RSI, MACD, moving averages) and "
        f"drawing tools for hands-on technical analysis.</div>"
        f"<a href='{tv_chart}' target='_blank' style='text-decoration:none;"
        f"background:{C['blue_border']};color:#ffffff;font-size:13px;font-weight:600;"
        f"padding:9px 18px;border-radius:6px;'>📈 Open Interactive Chart →</a>"
        f"<div style='font-size:10px;color:{C['muted']};margin-top:12px;'>"
        f"Opens tradingview.com in a new tab · Symbol: {tv_sym}</div>"
        f"</div>", unsafe_allow_html=True)

    # ── EDGAR LINK ─────────────────────────────────────────
    edgar_10k = f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={sym}&type=10-K&dateb=&owner=include&count=10&search_text="
    edgar_10q = f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={sym}&type=10-Q&dateb=&owner=include&count=10&search_text="
    edgar_url = edgar_10k
    st.markdown(
        f"<div style='background:{C['bg2']};border:1px solid {C['border']};"
        f"border-radius:8px;padding:10px 14px;display:flex;align-items:center;"
        f"gap:12px;margin-bottom:20px;'>"
        f"<span style='font-size:12px;color:{C['dim']};'>Official SEC Filings (10-K, 10-Q):</span>"
        f"<a href='{edgar_url}' target='_blank' style='font-size:12px;color:{C['blue']};"
        f"text-decoration:none;font-family:IBM Plex Mono,monospace;'>→ View on SEC EDGAR</a>"
        f"<span style='font-size:11px;color:{C['muted']};'>(Balance sheets, income statements, official filings)</span>"
        f"</div>", unsafe_allow_html=True)

    # ── COMPANY SUMMARY ────────────────────────────────────
    st.markdown(f"<div style='font-size:10px;font-weight:600;text-transform:uppercase;"
                f"letter-spacing:1px;color:{C['muted']};margin-bottom:12px;'>"
                f"📖 Company Overview</div>", unsafe_allow_html=True)

    desc_text = rrow.get("description","No description available.")
    ceo_name  = rrow.get("ceo","") or "—"
    emp_count = rrow.get("employees")
    website   = rrow.get("website","")
    emp_str   = f"{int(emp_count):,}" if emp_count else "N/A"

    # ── Para 1: full company description, trimmed at a sentence (not mid-word) ──
    def _trim_sentence(text, limit=550):
        text = (text or "").strip()
        if len(text) <= limit:
            return text
        cut = text[:limit]
        end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
        return (cut[:end+1] if end > limit * 0.4 else cut.rstrip() + "…")
    full_desc = (extras.get("description") or "").strip() or desc_text
    para1 = (_trim_sentence(full_desc) if full_desc and full_desc != "No description available."
             else f"{rrow.get('name',sym)} operates in the {rrow.get('sector','—')} sector.")

    # ── Para 2: leadership — clean, grammatical list (no more "led by X serves as…") ──
    def _fmt_exec(e):
        name  = (e.get("name") or "").strip()
        title = (e.get("title") or "").strip()
        if not name or not title:
            return None
        pay = e.get("pay")
        pay_s = f", ${pay/1e6:.1f}M comp" if isinstance(pay, (int, float)) and pay > 0 else ""
        return f"<b>{name}</b> — {title}{pay_s}"
    execs_fmt = [x for x in (_fmt_exec(e) for e in execs[:5]) if x]
    if execs_fmt:
        para2 = f"Key leadership at {rrow.get('name',sym)}: " + "; ".join(execs_fmt) + "."
    else:
        para2 = (f"{rrow.get('name',sym)} is led by its executive team"
                 + (f", including CEO {ceo_name}" if ceo_name and ceo_name != "—" else "") + ".")

    mktcap_s = rrow.get("mkt_cap","N/A")
    score_s  = f"{rrow.get('composite_score',0):.0f}/100"
    para3 = (f"{rrow.get('name',sym)} is a {rrow.get('sector','—')} company "
             f"classified in the {rrow.get('industry','—')} industry, "
             f"with a market capitalisation of {mktcap_s} and approximately "
             f"{emp_str} full-time employees. "
             f"{'The company website is ' + website + '.' if website else ''}")

    web_str = (f"<a href='{website}' target='_blank' style='color:{C['blue']};font-size:11px;'>"
               f"{website}</a>" if website else "")

    st.markdown(
        f"<div style='background:{C['bg2']};border:1px solid {C['border']};"
        f"border-radius:10px;padding:20px 22px;margin-bottom:20px;'>"
        f"<p style='font-size:13px;color:{C['dim']};line-height:1.75;margin-bottom:14px;'>{para1}</p>"
        f"<p style='font-size:13px;color:{C['dim']};line-height:1.75;margin-bottom:14px;'>{para2}</p>"
        f"<p style='font-size:13px;color:{C['dim']};line-height:1.75;margin-bottom:0;'>{para3}</p>"
        + (f"<div style='margin-top:10px;'>{web_str}</div>" if website else "")
        + f"</div>", unsafe_allow_html=True)

    # ── RECENT NEWS ──  (removed for now — re-enable by restoring the render
    #    block here; fetch_news() / news_ago() helpers are still defined above)

    # ── METRICS SECTION ────────────────────────────────────
    st.markdown(f"<div style='font-size:10px;font-weight:600;text-transform:uppercase;"
                f"letter-spacing:1px;color:{C['muted']};margin-bottom:12px;'>"
                f"📊 All Financial Metrics</div>", unsafe_allow_html=True)

    mc1, mc2 = st.columns(2)

    # VALUATION
    val_html = (
        f"<div style='background:{C['bg2']};border:1px solid {C['border']};"
        f"border-radius:10px;padding:16px 18px;height:100%;'>"
        + section_header("📊","Valuation")
        + metric_row("P / E Ratio",   fx(rrow.get("pe")),
                     vc(rrow.get("pe"),good=15,bad=40,inv=True),
                     "FMP/Yahoo", METRIC_DEFS["P/E Ratio"]["tip"],
                     tf="TTM")
        + metric_row("PEG Ratio",    fx(rrow.get("peg")),
                     vc(rrow.get("peg"),good=1,bad=2.5,inv=True),
                     "FMP/Yahoo", METRIC_DEFS["PEG Ratio"]["tip"],
                     tf="TTM")
        + metric_row("EV / EBITDA",  fx(rrow.get("ev_ebitda")),
                     vc(rrow.get("ev_ebitda"),good=12,bad=25,inv=True),
                     "FMP",  METRIC_DEFS["EV/EBITDA"]["tip"],
                     tf="TTM")
        + metric_row("Earnings Yield", fp(rrow.get("earnings_yield")),
                     vc(rrow.get("earnings_yield"),good=5,bad=2),
                     "FMP",  METRIC_DEFS["Earnings Yield"]["tip"],
                     tf="TTM")
        + metric_row("FCF Yield",    fp(rrow.get("fcf_yield")),
                     vc(rrow.get("fcf_yield"),good=4,bad=1),
                     "FMP",  METRIC_DEFS["FCF Yield"]["tip"],
                     tf="TTM")
        + metric_row("Price / Book", fx(rrow.get("pb")),
                     vc(rrow.get("pb"),   good=3, bad=10,  inv=True),
                     "FMP/Yahoo",
                     "Price divided by Book Value per share. Lower = cheaper relative to net assets. Software/tech often trades at high P/B — always compare within the same sector.",
                     tf="TTM")
        + metric_row("Price / FCF",  fx(rrow.get("pfcf")),
                     vc(rrow.get("pfcf"),  good=20, bad=40, inv=True),
                     "FMP/Yahoo",
                     "Price divided by Free Cash Flow per share. FCF is harder to manipulate than reported earnings, making this one of the most reliable valuation metrics. Below 20x is attractive for quality businesses.",
                     tf="TTM")
        + f"</div>"
    )

    # QUALITY
    qual_html = (
        f"<div style='background:{C['bg2']};border:1px solid {C['border']};"
        f"border-radius:10px;padding:16px 18px;height:100%;'>"
        + section_header("⚙️","Quality & Profitability")
        + metric_row("ROIC",         fp(rrow.get("roic")),
                     vc(rrow.get("roic"),good=15,bad=5),
                     "FMP/Yahoo", METRIC_DEFS["ROIC"]["tip"],
                     tf="TTM")
        + metric_row("ROE",          fp(rrow.get("roe")),
                     vc(rrow.get("roe"),good=15,bad=5),
                     "FMP/Yahoo", METRIC_DEFS["ROE"]["tip"],
                     tf="TTM")
        + metric_row("ROA",          fp(rrow.get("roa")),
                     vc(rrow.get("roa"),good=8,bad=2),
                     "FMP",  "Return on Assets — net income relative to total assets. Above 8% is solid.",
                     tf="TTM")
        + metric_row("Gross Margin", fp(rrow.get("gross_margin")),
                     vc(rrow.get("gross_margin"),good=50,bad=20),
                     "FMP/Yahoo", METRIC_DEFS["Gross Margin"]["tip"],
                     tf="TTM")
        + metric_row("Op Margin",    fp(rrow.get("op_margin")),
                     vc(rrow.get("op_margin"),good=20,bad=5),
                     "FMP/Yahoo", METRIC_DEFS["Operating Margin"]["tip"],
                     tf="TTM")
        + metric_row("Net Margin",   fp(rrow.get("net_margin")),
                     vc(rrow.get("net_margin"),good=15,bad=3),
                     "FMP/Yahoo", METRIC_DEFS["Net Margin"]["tip"],
                     tf="TTM")
        + f"</div>"
    )

    # GROWTH
    grow_html = (
        f"<div style='background:{C['bg2']};border:1px solid {C['border']};"
        f"border-radius:10px;padding:16px 18px;height:100%;'>"
        + section_header("📈","Growth")
        + metric_row("Rev Growth YoY", fp(rrow.get("rev_growth")),
                     vc(rrow.get("rev_growth"),good=15,bad=0),
                     "FMP",  METRIC_DEFS["Revenue Growth (YoY)"]["tip"],
                     tf="TTM")
        + metric_row("Rev 2yr CAGR",   fp(rrow.get("rev_cagr")),
                     vc(rrow.get("rev_cagr"),good=10,bad=0),
                     "FMP",  METRIC_DEFS["Rev 2yr CAGR"]["tip"],
                     tf="TTM")
        + metric_row("EPS Growth YoY", fp(rrow.get("eps_growth")),
                     vc(rrow.get("eps_growth"),good=15,bad=0),
                     "FMP",  "Earnings Per Share growth year-over-year. Faster than revenue = expanding margins. Slower = margin compression.",
                     tf="YoY Annual")
        + f"</div>"
    )

    # SAFETY
    safe_html = (
        f"<div style='background:{C['bg2']};border:1px solid {C['border']};"
        f"border-radius:10px;padding:16px 18px;height:100%;'>"
        + section_header("🛡️","Balance Sheet Safety")
        + metric_row("Debt / Equity",  fx(rrow.get("debt_equity")),
                     vc(rrow.get("debt_equity"),good=0.5,bad=2,inv=True),
                     "FMP/Yahoo", METRIC_DEFS["Debt/Equity"]["tip"],
                     tf="TTM")
        + metric_row("Current Ratio",  fx(rrow.get("current_ratio")),
                     vc(rrow.get("current_ratio"),good=1.5,bad=1.0),
                     "FMP",  METRIC_DEFS["Current Ratio"]["tip"],
                     tf="YoY Annual")
        + metric_row("Int. Coverage",  fx(rrow.get("int_coverage")),
                     vc(rrow.get("int_coverage"),good=5,bad=2),
                     "FMP",  METRIC_DEFS["Interest Coverage"]["tip"],
                     tf="2-Yr CAGR")
        + f"</div>"
    )

    with mc1:
        st.markdown(val_html,  unsafe_allow_html=True)
        st.markdown("<br>",    unsafe_allow_html=True)
        st.markdown(grow_html, unsafe_allow_html=True)
    with mc2:
        st.markdown(qual_html, unsafe_allow_html=True)
        st.markdown("<br>",    unsafe_allow_html=True)
        st.markdown(safe_html, unsafe_allow_html=True)

    # ── FINANCIAL STATEMENTS ───────────────────────────────
    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown(f"<div style='font-size:10px;font-weight:600;text-transform:uppercase;"
                f"letter-spacing:1px;color:{C['muted']};margin-bottom:12px;'>"
                f"📋 Financial Statements</div>", unsafe_allow_html=True)

    period = st.radio("Period", ["Annual","Quarterly"],
                      horizontal=True, label_visibility="collapsed")
    p = "annual" if period=="Annual" else "quarter"

    income_rows   = extras[f"income_{p}"]
    balance_rows  = extras[f"balance_{p}"]
    cashflow_rows = extras[f"cashflow_{p}"]

    tab1, tab2, tab3 = st.tabs(["📈 Income Statement","🏦 Balance Sheet","💵 Cash Flow"])

    with tab1:
        df = build_stmt_df(income_rows, {
            "Revenue":              "revenue",
            "Gross Profit":         "grossProfit",
            "Gross Margin %":       "grossProfitRatio",
            "Operating Income":     "operatingIncome",
            "Operating Margin %":   "operatingIncomeRatio",
            "EBITDA":               "ebitda",
            "Net Income":           "netIncome",
            "Net Margin %":         "netIncomeRatio",
            "EPS (Diluted)":        "epsdiluted",
            "R&D Expenses":         "researchAndDevelopmentExpenses",
        })
        if df is not None:
            st.dataframe(df, width='stretch')
            st.caption(f"Source: FMP Stable API (data sourced from SEC EDGAR filings)")
            st.markdown(f"[📄 View {sym} Annual Filings on SEC EDGAR]({edgar_10k})", unsafe_allow_html=False)
        else:
            st.info("Income statement data not available.",
                     tf="TTM")

    with tab2:
        df = build_stmt_df(balance_rows, {
            "Cash & Equivalents":       "cashAndCashEquivalents",
            "Total Current Assets":     "totalCurrentAssets",
            "Total Assets":             "totalAssets",
            "Total Current Liabilities":"totalCurrentLiabilities",
            "Short-Term Debt":          "shortTermDebt",
            "Long-Term Debt":           "longTermDebt",
            "Total Liabilities":        "totalLiabilities",
            "Shareholders Equity":      "totalStockholdersEquity",
            "Goodwill":                 "goodwill",
            "Intangible Assets":        "intangibleAssets",
        })
        if df is not None:
            st.dataframe(df, width='stretch')
            st.caption(f"Source: FMP Stable API (data sourced from SEC EDGAR filings)")
            st.markdown(f"[📄 View {sym} Annual Filings on SEC EDGAR]({edgar_10k})", unsafe_allow_html=False)
        else:
            st.info("Balance sheet data not available.")

    with tab3:
        df = build_stmt_df(cashflow_rows, {
            "Operating Cash Flow":  "operatingCashFlow",
            "Capital Expenditure":  "capitalExpenditure",
            "Free Cash Flow":       "freeCashFlow",
            "Dividends Paid":       "dividendsPaid",
            "Stock Buybacks":       "commonStockRepurchased",
            "Net Change in Cash":   "netChangeInCash",
            "D&A":                  "depreciationAndAmortization",
            "Stock-Based Comp":     "stockBasedCompensation",
        })
        if df is not None:
            st.dataframe(df, width='stretch')
            st.caption(f"Source: FMP Stable API (data sourced from SEC EDGAR filings)")
            st.markdown(f"[📋 View {sym} Quarterly Filings on SEC EDGAR]({edgar_10q})", unsafe_allow_html=False)
        else:
            st.info("Cash flow data not available.")

    # ── EARNINGS SECTION ──────────────────────────────────
    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown(
        f"<div style='font-size:10px;font-weight:600;text-transform:uppercase;"
        f"letter-spacing:1px;color:{C['muted']};margin-bottom:12px;'>📅 Earnings</div>",
        unsafe_allow_html=True)

    # Next earnings date card
    ned  = rrow.get("next_earnings_date","")
    ntime = rrow.get("next_earnings_time","")
    neps  = rrow.get("next_earnings_eps_est")
    if ned:
        from datetime import date as _date
        try:
            ned_dt   = datetime.strptime(ned[:10], "%Y-%m-%d").date()
            days_out = (ned_dt - _date.today()).days
            if days_out < 0:
                urgency_color = C["muted"]
                urgency_label = "Reported"
            elif days_out <= 14:
                urgency_color = C["amber"]
                urgency_label = f"⚠️ In {days_out} day{'s' if days_out!=1 else ''}"
            else:
                urgency_color = C["green"]
                urgency_label = f"In {days_out} days"
        except:
            urgency_color = C["dim"]; urgency_label = ""
        time_str  = {"amc":"After Market Close","bmo":"Before Market Open"}.get(ntime, ntime or "TBD")
        eps_str   = f"EPS Est: ${neps:.2f}" if neps else "EPS Est: TBD"
        st.markdown(
            f"<div style='background:{C['bg2']};border:1px solid {urgency_color}44;"
            f"border-radius:10px;padding:16px 20px;margin-bottom:16px;"
            f"display:flex;justify-content:space-between;align-items:center;'>"
            f"<div>"
            f"<div style='font-size:11px;color:{C['muted']};text-transform:uppercase;"
            f"letter-spacing:1px;margin-bottom:4px;'>Next Earnings Release</div>"
            f"<div style='font-family:IBM Plex Mono,monospace;font-size:22px;"
            f"font-weight:600;color:{C['text']};'>{ned[:10]}</div>"
            f"<div style='font-size:12px;color:{C['dim']};margin-top:4px;'>"
            f"{time_str} · {eps_str}</div>"
            f"</div>"
            f"<div style='text-align:right;'>"
            f"<span style='font-family:IBM Plex Mono,monospace;font-size:16px;"
            f"font-weight:600;color:{urgency_color};'>{urgency_label}</span>"
            f"</div></div>",
            unsafe_allow_html=True)
    else:
        st.markdown(
            f"<div style='background:{C['bg2']};border:1px solid {C['border']};"
            f"border-radius:10px;padding:14px 20px;margin-bottom:16px;"
            f"color:{C['muted']};font-size:13px;'>Next earnings date not available.</div>",
            unsafe_allow_html=True)

    # Earnings history table
    eh = extras.get("earnings_history", [])
    if eh:
        st.markdown(
            f"<div style='font-size:12px;font-weight:600;color:{C['dim']};"
            f"margin-bottom:8px;'>Historical Earnings Surprises</div>",
            unsafe_allow_html=True)
        from datetime import date as _date_hist
        _today_hist = _date_hist.today().strftime("%Y-%m-%d")
        rows_eh = []
        for e in eh:
            actual   = e.get("actualEarningResult") or e.get("actual")
            estimate = e.get("estimatedEarning")    or e.get("estimate")
            edate    = (e.get("date") or "")[:10]
            # Skip upcoming/unreported quarters — no actual result yet, or the
            # date is in the future. These belong to "Next Earnings", not history,
            # and were causing the historical table to disagree with it.
            if actual is None or (edate and edate > _today_hist):
                continue
            surprise = None
            if estimate is not None and estimate != 0:
                surprise = ((actual - estimate) / abs(estimate)) * 100
            beat = (actual >= estimate) if estimate is not None else None
            rows_eh.append({
                "Date":     edate,
                "EPS Actual":   f"${actual:.2f}",
                "EPS Estimate": f"${estimate:.2f}" if estimate is not None else "—",
                "Surprise":     f"{surprise:+.1f}%" if surprise is not None else "—",
                "Beat/Miss":    "✅ Beat" if beat else ("❌ Miss" if beat is False else "—"),
            })
        if not rows_eh:
            st.info("No reported earnings history available yet.")
        else:
            df_eh = pd.DataFrame(rows_eh)
            st.dataframe(df_eh, width='stretch', hide_index=True)
        # Beat rate summary
        beats = [r for r in rows_eh if r["Beat/Miss"] == "✅ Beat"]
        total_known = len([r for r in rows_eh if r["Beat/Miss"] in ("✅ Beat","❌ Miss")])
        if total_known > 0:
            beat_rate = len(beats) / total_known * 100
            bc = C["green"] if beat_rate >= 70 else C["amber"] if beat_rate >= 50 else C["red"]
            st.markdown(
                f"<div style='font-size:12px;color:{C['dim']};margin-top:6px;'>"
                f"Beat rate (last {total_known} quarters): "
                f"<span style='color:{bc};font-weight:600;'>{beat_rate:.0f}%</span></div>",
                unsafe_allow_html=True)
    else:
        st.info("Earnings history not available.")

    # ── LEGAL & LITIGATION ────────────────────────────────
    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown(
        f"<div style='font-size:10px;font-weight:600;text-transform:uppercase;"
        f"letter-spacing:1px;color:{C['muted']};margin-bottom:12px;'>⚖️ Legal & Litigation</div>",
        unsafe_allow_html=True)

    cik = rrow.get("cik","")
    with st.spinner("Loading legal data from SEC EDGAR…"):
        legal = fetch_legal_data(sym, cik)

    _has_data = legal.get("tenk_text") or legal.get("eightk_items")
    if not _has_data:
        cik_used = legal.get("cik_used","")
        cik_int_disp = int(cik_used) if cik_used else 0
        st.markdown(
            f"<div style='background:{C['bg2']};border:1px solid {C['border']};"
            f"border-radius:10px;padding:18px 22px;margin-bottom:12px;'>"
            f"<div style='font-size:13px;color:{C['dim']};line-height:1.75;'>"
            f"Legal proceedings data could not be automatically extracted for {sym}. "
            f"Review SEC filings directly for full legal disclosure.</div>"
            f"<div style='display:flex;gap:16px;margin-top:12px;'>"
            + (f"<a href='https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik_int_disp}&type=10-K&count=5' "
               f"target='_blank' style='font-size:12px;color:{C["blue"]};'>→ 10-K Filings on EDGAR</a>"
               f"<a href='https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik_int_disp}&type=8-K&count=10' "
               f"target='_blank' style='font-size:12px;color:{C["blue"]};'>→ 8-K Filings on EDGAR</a>"
               if cik_int_disp else "")
            + f"</div></div>",
            unsafe_allow_html=True)
    else:
        with st.spinner("Summarizing legal proceedings with AI…"):
            tenk_sum, _ = summarize_legal_with_claude(
                sym, legal.get("tenk_text",""), [])   # 8-K scan retired — 10-K only

        bg2=C["bg2"]; border=C["border"]; dim=C["dim"]; muted=C["muted"]
        amber=C["amber"]; green=C["green"]; text=C["text"]

        # 10-K Current Proceedings
        st.markdown(
            f"<div style='background:{bg2};border:1px solid {border};"
            f"border-radius:10px;padding:18px 22px;margin-bottom:12px;'>"
            f"<div style='font-size:11px;font-weight:600;text-transform:uppercase;"
            f"letter-spacing:1px;color:{muted};margin-bottom:10px;'>📄 Current Proceedings (10-K Item 3)</div>"
            + (f"<div style='font-size:13px;color:{dim};line-height:1.75;'>{tenk_sum}</div>"
               if tenk_sum else
               f"<div style='font-size:13px;color:{amber};'>⚠ Summary unavailable — could not reach the AI summarizer. Check your Anthropic API key in .streamlit/secrets.toml.</div>"
               if tenk_sum is None else
               f"<div style='font-size:13px;color:{muted};'>No material legal proceedings disclosed in latest 10-K.</div>")
            + f"<div style='font-size:10px;color:{muted};margin-top:10px;'>"
            f"Source: SEC EDGAR 10-K · CIK: {legal.get('cik_used','N/A')}</div>"
            f"</div>",
            unsafe_allow_html=True)

        # Recent Developments — plain EDGAR 8-K link (no keyword scan, no AI call).
        # The 10-K above carries the material legal disclosures; this is just a
        # recency escape-hatch to check filings made since the last annual report.
        _cik_used = legal.get("cik_used","")
        _eightk_url = (f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
                       f"&CIK={int(_cik_used)}&type=8-K&count=40") if _cik_used else ""
        st.markdown(
            f"<div style='background:{bg2};border:1px solid {border};"
            f"border-radius:10px;padding:18px 22px;margin-bottom:12px;'>"
            f"<div style='font-size:11px;font-weight:600;text-transform:uppercase;"
            f"letter-spacing:1px;color:{muted};margin-bottom:10px;'>🔴 Recent Developments (8-K Filings)</div>"
            f"<div style='font-size:13px;color:{dim};line-height:1.75;margin-bottom:12px;'>"
            f"The summary above reflects {sym}'s latest annual report (10-K). For any "
            f"material legal or corporate events disclosed since then, review the most "
            f"recent 8-K filings directly on SEC EDGAR.</div>"
            + (f"<a href='{_eightk_url}' target='_blank' style='text-decoration:none;"
               f"background:{C['bg3']};color:{C['blue']};font-size:13px;font-weight:600;"
               f"padding:9px 18px;border-radius:6px;border:1px solid {C['blue_border']};'>"
               f"→ View recent 8-K filings on EDGAR</a>"
               if _eightk_url else
               f"<div style='font-size:12px;color:{muted};'>CIK unavailable — search EDGAR for {sym} directly.</div>")
            + f"</div>",
            unsafe_allow_html=True)

        # EDGAR direct links
        cik_used = legal.get("cik_used","")
        if cik_used:
            cik_int = int(cik_used)
            st.markdown(
                f"<div style='display:flex;gap:12px;'>"
                f"<a href='https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik_int}&type=10-K&count=5' "
                f"target='_blank' style='font-size:12px;color:{C["blue"]};'>→ 10-K Filings on EDGAR</a>"
                f"<a href='https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik_int}&type=8-K&count=20' "
                f"target='_blank' style='font-size:12px;color:{C["blue"]};'>→ 8-K Filings on EDGAR</a>"
                f"</div>",
                unsafe_allow_html=True)

    # ── CHARTS ────────────────────────────────────────────
    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown(
        f"<div style='font-size:10px;font-weight:600;text-transform:uppercase;"
        f"letter-spacing:1px;color:{C['muted']};margin-bottom:12px;'>📊 Charts</div>",
        unsafe_allow_html=True)

    CHART_BORDER = [{"type":"rect","xref":"paper","yref":"paper",
                      "x0":0,"y0":0,"x1":1,"y1":1,
                      "line":{"color":"rgba(255,255,255,0.25)","width":1}}]
    DARK = dict(
        paper_bgcolor="#151b27", plot_bgcolor="#0e1015",
        font=dict(color="#ffffff", size=12, family="Inter, sans-serif"),
        margin=dict(l=60, r=20, t=60, b=50),
        xaxis=dict(gridcolor="rgba(255,255,255,0.06)", showgrid=True, zeroline=False,
                   tickfont=dict(color="#ffffff", size=12),
                   title_font=dict(color="#ffffff", size=13), color="#ffffff"),
        yaxis=dict(gridcolor="rgba(255,255,255,0.06)", showgrid=True, zeroline=False,
                   tickfont=dict(color="#ffffff", size=12),
                   title_font=dict(color="#ffffff", size=13), color="#ffffff"),
        legend=dict(font=dict(color="#ffffff", size=12), bgcolor="rgba(0,0,0,0)",
                    orientation="h", y=1.08, x=0),
        shapes=CHART_BORDER,
    )

    # ── Chart 1: Revenue Trend — full width ──────────────────
    rows = extras["income_annual"]
    if rows:
        dates = [r.get("date","")[:4] for r in reversed(rows)]
        revs  = [(r.get("revenue")    or 0)/1e9 for r in reversed(rows)]
        gprof = [(r.get("grossProfit") or 0)/1e9 for r in reversed(rows)]
        net_i = [(r.get("netIncome")  or 0)/1e9 for r in reversed(rows)]
        fig = go.Figure()
        fig.add_trace(go.Bar(name="Revenue",      x=dates, y=revs,
                             marker_color="#3b82f6", marker_line_width=0))
        fig.add_trace(go.Bar(name="Gross Profit", x=dates, y=gprof,
                             marker_color="#10b981", marker_line_width=0))
        fig.add_trace(go.Bar(name="Net Income",   x=dates, y=net_i,
                             marker_color="#f59e0b", marker_line_width=0))
        fig.update_layout(**DARK, barmode="group", height=380)
        fig.update_yaxes(title_text="USD Billions", tickprefix="$")
        fig.update_xaxes(tickfont=dict(size=13))
        st.markdown("<div style='font-size:15px;font-weight:600;color:#ffffff;margin-bottom:6px;'>📈 Revenue Trend ($B)</div>", unsafe_allow_html=True)
        st.plotly_chart(fig, width='stretch')
    else:
        st.info("Revenue data not available.")

    # ── Chart 2: Margin Expansion — full width ────────────────
    if rows:
        # Calculate from actual values — don't rely on ratio fields
        def safe_margin(r, num_key, denom_key="revenue"):
            n = r.get(num_key) or 0; d = r.get(denom_key) or 1
            return (n / d * 100) if d else 0
        dates = [r.get("date","")[:4] for r in reversed(rows)]
        gm_   = [safe_margin(r,"grossProfit")    for r in reversed(rows)]
        om_   = [safe_margin(r,"operatingIncome") for r in reversed(rows)]
        nm_   = [safe_margin(r,"netIncome")       for r in reversed(rows)]
        fig = go.Figure()
        fig.add_trace(go.Scatter(name="Gross Margin", x=dates, y=gm_,
                                  mode="lines+markers",
                                  line=dict(color="#10b981", width=2.5),
                                  marker=dict(size=8, color="#10b981")))
        fig.add_trace(go.Scatter(name="Operating Margin", x=dates, y=om_,
                                  mode="lines+markers",
                                  line=dict(color="#3b82f6", width=2.5),
                                  marker=dict(size=8, color="#3b82f6")))
        fig.add_trace(go.Scatter(name="Net Margin", x=dates, y=nm_,
                                  mode="lines+markers",
                                  line=dict(color="#f59e0b", width=2.5),
                                  marker=dict(size=8, color="#f59e0b")))
        fig.update_layout(**DARK, height=380)
        fig.update_yaxes(title_text="Margin %", ticksuffix="%")
        fig.update_xaxes(tickfont=dict(size=13))
        st.markdown("<div style='font-size:15px;font-weight:600;color:#ffffff;margin-bottom:6px;'>📉 Margin Expansion (%)</div>", unsafe_allow_html=True)
        st.plotly_chart(fig, width='stretch')

    # ── Chart: Peer Comparison — full width ───────────────────
    all_data_peers = st.session_state.universe_data or []
    sector   = rrow.get("sector","")
    peers    = [d for d in all_data_peers if d.get("sector")==sector and d["symbol"]!=sym]
    compare  = ([rrow] + peers)[:10]
    if len(compare) > 1:
        metrics_cmp = [
            ("ROIC %",         "roic"),
            ("Gross Margin %",  "gross_margin"),
            ("Op Margin %",     "op_margin"),
            ("Revenue Growth %","rev_growth"),
            ("Net Margin %",    "net_margin"),
        ]
        selected_cmp = st.selectbox(
            "Peer comparison metric",
            [m[0] for m in metrics_cmp],
            key="peer_cmp_metric")
        field_cmp = next(f for lbl,f in metrics_cmp if lbl==selected_cmp)
        labels    = [d["symbol"] for d in compare]
        vals_cmp  = [d.get(field_cmp) or 0 for d in compare]
        bar_colors= ["#3b82f6" if d["symbol"]==sym else "#334d70" for d in compare]
        fig = go.Figure(go.Bar(
            x=labels, y=vals_cmp,
            marker_color=bar_colors, marker_line_width=0,
            text=[f"{v:.1f}%" for v in vals_cmp],
            textposition="outside",
            textfont=dict(size=12, color="#dce8f8")))
        fig.update_layout(**DARK, height=380, showlegend=False)
        fig.update_yaxes(title_text=selected_cmp, ticksuffix="%")
        fig.update_xaxes(tickfont=dict(size=13))
        st.markdown(f"<div style='font-size:15px;font-weight:600;color:#ffffff;margin-bottom:6px;'>🏆 {selected_cmp} — {sym} vs {sector} Peers</div>", unsafe_allow_html=True)
        st.plotly_chart(fig, width='stretch')

    # ── PROJECTIONS (coming soon) ─────────────────────────
    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown(
        f"<div style='font-size:10px;font-weight:600;text-transform:uppercase;"
        f"letter-spacing:1px;color:{C['muted']};margin-bottom:12px;'>🔮 Financial Projections</div>",
        unsafe_allow_html=True)
    st.markdown(
        f"<div style='background:{C['bg2']};border:1px dashed {C['border2']};"
        f"border-radius:10px;padding:24px 22px;text-align:center;margin-bottom:12px;'>"
        f"<div style='font-size:15px;font-weight:600;color:{C['dim']};'>"
        f"Financial projections coming soon</div></div>", unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────
# LOADING PAGE  — runs on every fresh app launch
# ─────────────────────────────────────────────────────────────
def show_loading_page():
    bg2=C["bg2"]; blue=C["blue"]; dim=C["dim"]; border=C["border"]
    st.markdown(
        f"<div style='max-width:640px;margin:60px auto 0 auto;text-align:center;'>"
        f"<div style='font-family:IBM Plex Mono,monospace;font-size:28px;font-weight:600;"
        f"color:{blue};margin-bottom:8px;'>📈 Stock Screener</div>"
        f"<div style='font-size:14px;color:{dim};margin-bottom:40px;'>"
        f"Downloading live market data — this happens once per session</div>"
        f"</div>", unsafe_allow_html=True)

    status = st.empty()
    bar    = st.progress(0)
    detail = st.empty()

    def update(pct, msg, sub=""):
        bar.progress(pct)
        status.markdown(
            f"<div style='text-align:center;font-size:14px;font-weight:500;"
            f"color:{C["text"]};margin:12px 0 4px 0;'>{msg}</div>",
            unsafe_allow_html=True)
        if sub:
            detail.markdown(
                f"<div style='text-align:center;font-size:12px;color:{C["dim"]};'>{sub}</div>",
                unsafe_allow_html=True)

    # Step 1: Pull S&P 1500 ticker symbols
    # S&P 500 + NASDAQ 100 → FMP (authoritative, live)
    # S&P 400 + S&P 600   → Wikipedia (FMP doesn't carry these lists)

    def wiki_tickers(url, cols):
        """Fetch Wikipedia S&P index page with requests, then parse with pd.read_html.
        Tries multiple column name variations and picks the best table."""
        import io, re as _re

        def clean_syms(raw):
            out = []
            for s in raw:
                s = str(s).strip().replace(".", "-").replace(" ","")
                s = s.split()[0] if s.split() else s
                if (1 < len(s) <= 6
                        and s.replace("-","").isalpha()
                        and s.upper() not in ("TICKER","SYMBOL","N/A","NAN","NONE","CIK","SEC")):
                    out.append(s.upper())
            return out

        # Try multiple user agents
        for ua in [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "python-requests/2.31.0",
        ]:
            try:
                resp = requests.get(url, timeout=20, headers={"User-Agent": ua})
                if resp.status_code != 200:
                    continue

                # If raw wiki format, extract tickers with regex
                if "action=raw" in url:
                    matches = _re.findall(r'\|\s*([A-Z]{1,5}(?:-[A-Z]{1,2})?)\s*\|', resp.text)
                    if len(matches) > 50:
                        return list(dict.fromkeys(matches))
                    continue

                tables = pd.read_html(io.StringIO(resp.text))
                best = []
                for table in tables:
                    # Try explicit column names + any column with ticker/symbol in name
                    for col in list(table.columns):
                        cl = str(col).lower().strip()
                        if any(kw in cl for kw in ("ticker","symbol")):
                            candidates = clean_syms(table[col].dropna().tolist())
                            if len(candidates) > len(best):
                                best = candidates
                    # Fallback: first column of large tables
                    if len(best) < 50 and len(table) > 100:
                        candidates = clean_syms(table.iloc[:,0].dropna().tolist())
                        if len(candidates) > len(best):
                            best = candidates
                if len(best) > 50:
                    return best
            except Exception:
                continue
        return []

    # FMP — S&P 500 (503 companies)
    update(0.02, "Fetching S&P 500 from FMP…", "Live constituent list")
    raw500 = fmp("sp500-constituent") or []
    sp500  = [x.get("symbol") for x in raw500 if isinstance(x,dict) and x.get("symbol")]

    # FMP — NASDAQ 100 (101 companies, adds any not already in S&P 500)
    update(0.04, f"S&P 500: {len(sp500)} tickers. Fetching NASDAQ 100 from FMP…")
    rawnq  = fmp("nasdaq-constituent") or []
    nasdaq = [x.get("symbol") for x in rawnq if isinstance(x,dict) and x.get("symbol")]

    # FMP company-screener — every US-listed operating company above the
    # market-cap floor. This is the "catch them early" universe: small/mid-caps
    # that aren't in any index yet still get full fundamentals.
    update(0.06, f"NASDAQ 100: {len(nasdaq)} tickers. Scanning market for opportunities…",
           f"US-listed companies above {fm(UNIVERSE_MARKETCAP_FLOOR)}")
    screener = fetch_screener_universe(UNIVERSE_MARKETCAP_FLOOR)

    # Build a lookup: ticker → which index(es) it belongs to. Screener-only names
    # get no index tag (they surface when no Index filter is applied).
    ticker_index_map = {}
    for s in sp500:
        ticker_index_map.setdefault(s, set()).add("S&P 500")
    for s in nasdaq:
        ticker_index_map.setdefault(s, set()).add("NASDAQ 100")
    st.session_state["ticker_index_map"] = ticker_index_map

    tickers = list(dict.fromkeys(sp500 + nasdaq + screener))

    # Fallback: hardcoded ~600 if Wikipedia is unreachable
    if len(tickers) < 100:
        update(0.07, "Wikipedia unavailable — using hardcoded S&P 500 list…")
        tickers = list(dict.fromkeys([
            "AAPL","MSFT","NVDA","AVGO","ORCL","CSCO","ADBE","ACN","IBM","INTU",
            "TXN","QCOM","AMD","AMAT","NOW","MU","KLAC","LRCX","SNPS","CDNS",
            "ADI","MCHP","NXPI","HPQ","HPE","INTC","PANW","CRWD","FTNT","CTSH",
            "IT","GPN","FISV","FIS","PAYX","ADP","ROP","ANSS","VRSN","GLW",
            "TER","KEYS","WDC","STX","NTAP","JNPR","FFIV","AKAM","PTC","LDOS",
            "SAIC","FTV","EPAM","MPWR","ENTG","ZBRA","GDDY","ANET","MRVL","TRMB",
            "META","GOOGL","GOOG","AMZN","TSLA","NFLX","BKNG","EBAY","UBER","ABNB",
            "DDOG","ZS","NET","WDAY","VEEV","HUBS","PLTR","SNOW","MDB","PAYC",
            "JPM","BAC","WFC","GS","MS","C","USB","PNC","TFC","COF",
            "MTB","RF","FITB","HBAN","KEY","CFG","ZION","ALLY","DFS","SYF",
            "BLK","SCHW","AXP","ICE","CME","NDAQ","CBOE","MSCI","SPGI","MCO",
            "FDS","VRSK","V","MA","PYPL","FI","KKR","BX","APO","CG",
            "BRK-B","MET","PRU","AFL","ALL","PGR","TRV","CB","AIG","HIG",
            "ACGL","MMC","AON","WTW","AJG","BRO","ERIE","CNA","L","RLI",
            "JNJ","LLY","ABBV","MRK","BMY","AMGN","GILD","VRTX","REGN","BIIB",
            "ABT","TMO","DHR","ISRG","BSX","SYK","MDT","EW","ZBH","BDX",
            "BAX","RMD","DXCM","PODD","ALGN","HOLX","IQV","A","MTD","IDXX",
            "UNH","CVS","CI","HUM","CNC","MOH","ELV","HCA","MCK","CAH","ABC",
            "PG","KO","PEP","PM","MO","MDLZ","KMB","CL","GIS","K",
            "CAG","HRL","MKC","CHD","CLX","WMT","COST","TGT","KDP","STZ",
            "HD","LOW","TJX","ROST","BURL","NKE","LULU","DECK","RH","SKX",
            "MCD","SBUX","CMG","YUM","DRI","TXRH","GM","F","APTV","MAR",
            "HLT","DIS","LVS","WYNN","MGM","RCL","CCL","ETSY","LYFT","ABNB",
            "GE","HON","MMM","CAT","DE","EMR","ETN","PH","ROK","AME",
            "ITW","SWK","SNA","DOV","PNR","XYL","IDEX","IR","GNRC","BA",
            "LMT","RTX","NOC","GD","HII","TDG","AXON","UPS","FDX","CSX",
            "UNP","NSC","ODFL","JBHT","CHRW","XPO","PWR","CARR","OTIS","TT",
            "PHM","DHI","LEN","TOL","MDC","KBH","BLDR","VMC","MLM","EME",
            "XOM","CVX","COP","EOG","PXD","DVN","OXY","APA","SLB","HAL",
            "BKR","WMB","KMI","OKE","VLO","PSX","MPC","TRGP","NEE","DUK",
            "SO","D","AEP","EXC","XEL","ES","PEG","WEC","ED","EIX",
            "ETR","FE","PPL","CMS","AES","NRG","AWK","CEG","PLD","AMT",
            "EQIX","CCI","SBAC","DLR","O","VICI","SPG","EQR","AVB","ESS",
            "MAA","VTR","WELL","EXR","PSA","LIN","APD","PPG","SHW","ECL",
            "NUE","STLD","FCX","NEM","CF","MOS","CTVA","IP","PKG","AMCR",
            "DOW","DD","LYB","ALB","T","VZ","TMUS","CHTR","CMCSA","EA",
            "TTWO","OMC","IPG","PARA","WBD","FOX","FOXA","LYV","ZTS",
        ]))

    # DEV_MODE: hardcoded top 40 by market cap for fast testing
    if DEV_MODE:
        tickers = [
            "AAPL","MSFT","NVDA","AMZN","GOOGL","META","TSLA","BRK-B","AVGO","JPM",
            "LLY","V","UNH","XOM","MA","JNJ","PG","HD","COST","ABBV",
            "MRK","BAC","NFLX","CRM","CVX","KO","PEP","TMO","ACN","MCD",
            "AMD","ADBE","WMT","LIN","CSCO","ABT","TXN","DHR","MS","GS",
        ]

    n = len(tickers)
    update(0.10, f"Universe: {n} companies ready — downloading financial data…",
           f"S&P 500: {len(sp500)} · NASDAQ 100: {len(nasdaq)} · "
           f"Market ({fm(UNIVERSE_MARKETCAP_FLOOR)}+): {len(screener)}")

    # Step 2: Fetch companies in PARALLEL, FMP-only. Batch endpoints aren't on
    # this plan, so each company still needs its own calls — but 12 concurrent
    # workers bring the full ~520-name universe down to ~5 min, and dropping the
    # per-ticker yfinance calls removes the main slowdown + rate-limit risk.
    update(0.11, "Fetching upcoming earnings calendar…", "One bulk call for the whole universe")
    earnings_map = fetch_earnings_map()

    universe = []
    done = 0
    with ThreadPoolExecutor(max_workers=12) as ex:
        futures = {ex.submit(fetch_universe_row, sym): sym for sym in tickers}
        for fut in as_completed(futures):
            sym  = futures[fut]
            done += 1
            pct  = 0.11 + (done / n) * 0.87
            if done % 5 == 0 or done == n:
                update(pct, f"Downloading financial data… {done}/{n} companies",
                       f"Loaded: {len(universe)}")
            try:
                row = fut.result()
            except Exception:
                row = None
            if row:
                e = earnings_map.get(sym, {})
                row["next_earnings_date"]    = e.get("date", "")
                row["next_earnings_eps_est"] = e.get("epsEstimated")
                row["next_earnings_time"]    = e.get("time", "")
                row["indexes"] = list(ticker_index_map.get(sym, set()))
                universe.append(row)

    # Deduplicate by company name — keep the ticker with the higher market cap
    # (handles dual share classes like GOOGL/GOOG, BRK-A/BRK-B, FOX/FOXA)
    seen_names = {}
    deduped = []
    for row in universe:
        name = (row.get("name") or row["symbol"]).strip().lower()
        mc   = row.get("mkt_cap_raw") or 0
        try: mc = float(mc)
        except: mc = 0
        if name not in seen_names:
            seen_names[name] = (len(deduped), mc)
            deduped.append(row)
        else:
            prev_idx, prev_mc = seen_names[name]
            if mc > prev_mc:
                seen_names[name] = (prev_idx, mc)
                deduped[prev_idx] = row  # replace with higher mkt cap ticker
    universe = deduped

    st.session_state.universe_data   = universe
    st.session_state.universe_loaded = len(universe) > 0
    st.session_state["ticker_index_map"] = ticker_index_map
    universe_cache_save(universe, ticker_index_map)  # persist for browser refresh
    pf_refresh(universe)  # update portfolio data with fresh metrics

    if len(universe) == 0:
        st.error("No company data loaded. Check your FMP API key.")
        st.stop()

    bar.progress(1.0)
    status.markdown(
        f"<div style='text-align:center;font-size:15px;font-weight:600;"
        f"color:{C["green"]};margin:12px 0;'>"
        f"✅  {len(universe)} companies loaded and ready</div>",
        unsafe_allow_html=True)
    detail.empty()
    time.sleep(1.2)
    st.rerun()


# ─────────────────────────────────────────────────────────────
# FILTER & RANK ENGINE
# ─────────────────────────────────────────────────────────────
def apply_metric_filters(data, metric_states, hl_priority_order, range_priority_order):
    """
    1. Apply all Range filters as hard AND filters
    2. Sort by primary H/L metric (first in hl_priority_order)
       OR by first range metric descending if no H/L selected
    3. Add ranking columns for secondary H/L metrics
    Returns: (filtered+sorted list, secondary_rankings dict)
    """
    enabled = {k: v for k, v in metric_states.items() if v.get("enabled")}
    if not enabled or not data:
        return list(data), {}

    df = pd.DataFrame(data)

    # Step 1 — Range hard filters
    range_active = [k for k in range_priority_order if k in enabled and enabled[k]["mode"]=="range"]
    for name in range_active:
        cfg   = enabled[name]
        field = METRIC_DEFS[name]["field"]
        if field not in df.columns:
            continue
        col = pd.to_numeric(df[field], errors="coerce")
        mask = col.notna()
        if cfg.get("range_min") is not None:
            mask &= col >= cfg["range_min"]
        if cfg.get("range_max") is not None:
            mask &= col <= cfg["range_max"]
        df = df[mask].copy()

    if df.empty:
        return [], {}

    # Step 2 — Sort
    hl_active = [n for n in hl_priority_order
                 if n in enabled and enabled[n]["mode"] in ("highest","lowest")]
    secondary_rankings = {}

    if hl_active:
        primary = hl_active[0]
        pf      = METRIC_DEFS[primary]["field"]
        asc     = (enabled[primary]["mode"] == "lowest")
        # Remove rows where primary metric is NaN — no data = no ranking
        df = df[pd.to_numeric(df[pf], errors="coerce").notna()].copy()
        if df.empty:
            return [], {}
        df = df.sort_values(pf, ascending=asc, na_position="last")
        # Secondary H/L → rank columns
        for sec in hl_active[1:]:
            sf  = METRIC_DEFS[sec]["field"]
            asc2 = (enabled[sec]["mode"] == "lowest")
            df[f"__rank_{sf}"] = df[sf].rank(
                ascending=asc2, na_option="bottom", method="min").astype(int)
            secondary_rankings[sec] = f"__rank_{sf}"
    elif range_active:
        # Only ranges — sort descending by first range metric
        pf = METRIC_DEFS[range_active[0]]["field"]
        df = df[pd.to_numeric(df[pf], errors="coerce").notna()].copy()
        df = df.sort_values(pf, ascending=False, na_position="last")

    return df.reset_index(drop=True).to_dict("records"), secondary_rankings


def build_search_card(r):
    """Simplified card for search results — no filter context needed."""
    bg2=C["bg2"]; brd="rgba(255,255,255,0.1)"; txt=C["text"]
    dim=C["dim"];  blue=C["blue"]; bg3=C["bg3"]; cdim=C["dim"]

    def pill(label, val, color):
        return (f"<div style='flex:1;background:{bg3};border-radius:8px;"
                f"padding:10px 12px;min-width:90px;'>"
                f"<div style='font-size:10px;color:{cdim};text-transform:uppercase;"
                f"letter-spacing:1px;margin-bottom:4px;'>{label}</div>"
                f"<div style='font-family:IBM Plex Mono,monospace;font-size:14px;"
                f"font-weight:600;color:{color};'>{val}</div>"
                f"</div>")

    roic_c  = vc(r.get("roic"),         good=15, bad=5)
    rev_c   = vc(r.get("rev_growth"),   good=15, bad=0)
    gm_c    = vc(r.get("gross_margin"), good=50, bad=20)
    pe_c    = vc(r.get("pe"),           good=15,  bad=40, inv=True)

    pills = (
        pill("ROIC",        fp(r.get("roic")),         roic_c) +
        pill("Rev Growth",  fp(r.get("rev_growth")),   rev_c)  +
        pill("Gross Margin",fp(r.get("gross_margin")), gm_c)   +
        pill("P/E",         fx(r.get("pe")),            pe_c)
    )

    return (
        f"<div style='background:{bg2};border:1.5px solid {brd};"
        f"border-radius:12px;padding:16px 20px;margin-bottom:4px;'>"
        f"<div style='display:flex;justify-content:space-between;align-items:flex-start;"
        f"margin-bottom:14px;'>"
        f"<div>"
        f"<div style='font-family:IBM Plex Mono,monospace;font-size:22px;"
        f"font-weight:700;color:{txt};'>{r['symbol']}</div>"
        f"<div style='font-size:13px;color:{txt};margin-top:2px;'>{r['name']}</div>"
        f"<div style='font-size:11px;color:{dim};margin-top:2px;'>"
        f"{r['sector']} · {r['industry']}</div>"
        f"</div>"
        f"<div style='text-align:right;'>"
        f"<div style='font-family:IBM Plex Mono,monospace;font-size:16px;"
        f"font-weight:600;color:{blue};'>{r['mkt_cap']}</div>"
        f"<div style='font-size:13px;color:{txt};margin-top:3px;'>{r['price']}</div>"
        f"</div></div>"
        f"<div style='display:flex;gap:8px;flex-wrap:wrap;'>{pills}</div>"
        f"</div>"
    )


# ─────────────────────────────────────────────────────────────
# PORTFOLIO PAGE
# ─────────────────────────────────────────────────────────────
def show_portfolio_page():
    bg2=C["bg2"]; blue=C["blue"]; text=C["text"]; dim=C["dim"]
    brd="rgba(255,255,255,0.1)"; muted=C["muted"]
    bg3=C["bg3"]; amber=C["amber"]; green=C["green"]; red=C["red"]

    # Nav
    nav1, nav2 = st.columns([1,1])
    with nav1:
        if st.button("📈 Screener", width='stretch'):
            st.session_state.page = "screener"; st.rerun()
    with nav2:
        st.button("My Portfolio", width='stretch', disabled=True)
    st.markdown("<br>", unsafe_allow_html=True)

    companies = pf_load()

    st.markdown(
        f"<div style='font-family:IBM Plex Mono,monospace;font-size:22px;"
        f"font-weight:600;color:{blue};margin-bottom:4px;'>My Portfolio</div>"
        f"<div style='font-size:13px;color:#ffffff;margin-bottom:20px;'>"
        f"{len(companies)} compan{'y' if len(companies)==1 else 'ies'} saved</div>",
        unsafe_allow_html=True)

    if not companies:
        st.info("No companies saved yet. Click Add to Portfolio on any Due Diligence page.")
        return

    for i, co in enumerate(companies):
        sym  = co["symbol"]
        data = co.get("data", {})
        r    = data  # treat saved data like a screener row

        def pill(label, val, color):
            return (f"<div style='flex:1;background:{bg3};border-radius:8px;"
                    f"padding:10px 12px;min-width:90px;'>"
                    f"<div style='font-size:10px;color:{dim};text-transform:uppercase;"
                    f"letter-spacing:1px;margin-bottom:4px;'>{label}</div>"
                    f"<div style='font-family:IBM Plex Mono,monospace;font-size:14px;"
                    f"font-weight:600;color:{color};'>{val}</div>"
                    f"</div>")

        roic_c = vc(r.get("roic"),         good=15, bad=5)
        rev_c  = vc(r.get("rev_growth"),   good=15, bad=0)
        gm_c   = vc(r.get("gross_margin"), good=50, bad=20)
        pe_c   = vc(r.get("pe"),           good=15,  bad=40, inv=True)

        pills = (pill("ROIC",         fp(r.get("roic")),         roic_c) +
                 pill("Rev Growth",   fp(r.get("rev_growth")),   rev_c)  +
                 pill("Gross Margin", fp(r.get("gross_margin")), gm_c)   +
                 pill("P/E",          fx(r.get("pe")),            pe_c))

        added = co.get("added_date","")
        in_universe = any(d["symbol"]==sym for d in (st.session_state.universe_data or []))
        status_badge = (
            f"<span style='font-size:10px;padding:2px 7px;border-radius:4px;"
            f"background:#162520;color:{green};'>● Live data</span>"
            if in_universe else
            f"<span style='font-size:10px;padding:2px 7px;border-radius:4px;"
            f"background:#221414;color:{amber};'>● Saved data</span>"
        )

        st.markdown(
            f"<div style='background:{bg2};border:1.5px solid {brd};"
            f"border-radius:12px;padding:18px 20px;margin-bottom:4px;'>"
            f"<div style='display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:14px;'>"
            f"<div>"
            f"<div style='display:flex;align-items:center;gap:10px;'>"
            f"<div style='font-family:IBM Plex Mono,monospace;font-size:22px;font-weight:700;color:{text};'>{sym}</div>"
            f"{status_badge}</div>"
            f"<div style='font-size:13px;color:{text};margin-top:2px;'>{co.get('name',sym)}</div>"
            f"<div style='font-size:11px;color:{dim};'>{co.get('sector','—')} · Added {added}</div>"
            f"</div>"
            f"<div style='text-align:right;'>"
            f"<div style='font-family:IBM Plex Mono,monospace;font-size:16px;font-weight:600;color:{blue};'>{fm(r.get('mkt_cap_raw'))}</div>"
            f"<div style='font-size:13px;color:{text};margin-top:3px;'>{r.get('price','—')}</div>"
            f"</div></div>"
            f"<div style='display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px;'>{pills}</div>"
            f"</div>",
            unsafe_allow_html=True)

        # Notes
        note_key = f"notes_{sym}_{i}"
        current_notes = co.get("notes","")
        note_val = st.text_area(
            f"📝 Notes — {sym}",
            value=current_notes,
            height=80,
            key=note_key,
            placeholder="Add your investment thesis, price target, or any notes here…")

        btn1, btn2, btn3 = st.columns([2,2,1])
        with btn1:
            if st.button(f"💾 Save Notes", key=f"savenote_{sym}_{i}", width='stretch'):
                pf_save_notes(sym, note_val)
                st.success("Notes saved.")
        with btn2:
            if st.button(f"📋 View Due Diligence — {sym}", key=f"pf_dd_{sym}_{i}", width='stretch'):
                st.session_state.selected_symbol = sym
                st.session_state.page = "dd"; st.rerun()
        with btn3:
            if st.button("🗑 Remove", key=f"pf_rm_{sym}_{i}", width='stretch'):
                pf_remove(sym)
                st.rerun()
        st.markdown("<br>", unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────
# SCREENER PAGE
# ─────────────────────────────────────────────────────────────
def show_screener_page():
    univ = st.session_state.universe_data or []

    # ── Apply index + sector pre-filters (set by sidebar, read after sidebar renders)
    # These are read from session state so they persist correctly across reruns
    _sel_idx = st.session_state.get("filter_indexes", [])
    _sel_sec = st.session_state.get("filter_sectors", [])
    if _sel_idx:
        _idx_map = st.session_state.get("ticker_index_map", {})
        def _seg_match(r):
            tags = _idx_map.get(r["symbol"], set())
            try: mc = float(r.get("mkt_cap_raw") or 0)
            except (TypeError, ValueError): mc = 0
            tier = ("Large Cap ($10B+)" if mc >= 10e9
                    else "Mid Cap ($2–10B)" if mc >= 2e9
                    else "Small Cap (<$2B)")
            return any(s in tags or s == tier for s in _sel_idx)
        univ = [r for r in univ if _seg_match(r)]
    if _sel_sec:
        univ = [r for r in univ if r.get("sector","—") in _sel_sec]
    # Pre-revenue companies are EXCLUDED by default when screening by Revenue
    # Growth; the "Include pre-revenue" checkbox opts them back in.
    _ms_now = st.session_state.get("metric_states") or {}
    if (_ms_now.get("Revenue Growth (YoY)", {}).get("enabled")
            and not st.session_state.get("include_prerev")):
        univ = [r for r in univ if not r.get("pre_revenue")]

    n_univ = len(univ)

    # ── Init metric states ──────────────────────────────────
    DEFAULTS = ["Revenue Growth (YoY)","ROIC","Gross Margin","Earnings Yield","FCF Yield"]
    if st.session_state.metric_states is None:
        st.session_state.metric_states = {
            name: {"enabled": False, "mode": "highest",
                   "range_min": None, "range_max": None}
            for name in METRIC_DEFS
        }
        st.session_state.hl_priority_order    = []
        st.session_state.range_priority_order = []

    ms  = st.session_state.metric_states
    hlp = st.session_state.hl_priority_order
    rp  = st.session_state.range_priority_order

    BADGE = {1:" [1]",2:" [2]",3:" [3]",4:" [4]",5:" [5]",
             6:" [6]",7:" [7]",8:" [8]",9:" [9]",10:" [10]"}

    # ── SIDEBAR ─────────────────────────────────────────────
    with st.sidebar:
        st.markdown("<div style='font-size:13px;font-weight:600;color:#1a2332;"
                    "margin-bottom:2px;'>📊 Select Metrics</div>", unsafe_allow_html=True)
        st.markdown(f"<div style='font-size:14px;font-weight:500;color:#1a1a1a;margin-bottom:10px;'>"
                    f"Universe: {n_univ} companies</div>", unsafe_allow_html=True)
        st.markdown("---")

        # ── INDEX / MARKET-CAP SEGMENT FILTER ───────────────
        st.markdown("<div style='font-size:12px;font-weight:700;color:#1a2332;"
                    "margin-bottom:4px;'>🗂 Index / Market Cap</div>", unsafe_allow_html=True)
        all_indexes = ["S&P 500", "NASDAQ 100",
                       "Large Cap ($10B+)", "Mid Cap ($2–10B)", "Small Cap (<$2B)"]
        sel_indexes = st.multiselect("Index", all_indexes, default=[],
                                     key="filter_indexes",
                                     placeholder="All companies",
                                     label_visibility="collapsed")

        # ── SECTOR FILTER ───────────────────────────────────
        st.markdown("<div style='font-size:12px;font-weight:700;color:#1a2332;"
                    "margin-bottom:4px;margin-top:8px;'>🏭 Sector</div>", unsafe_allow_html=True)
        all_sectors = sorted({r.get("sector","—") for r in univ if r.get("sector","—") != "—"})
        sel_sectors = st.multiselect("Sector", all_sectors, default=[],
                                     key="filter_sectors",
                                     placeholder="All sectors",
                                     label_visibility="collapsed")
        st.markdown("---")

        for mname, defn in METRIC_DEFS.items():
            cfg = ms[mname]

            # Badge label
            badge = ""
            if cfg["enabled"] and cfg["mode"] in ("highest","lowest"):
                if mname in hlp:
                    rank = hlp.index(mname)+1
                    badge = " " + BADGE.get(rank, f"#{rank}")

            checked = st.checkbox(f"{mname}{badge}", value=cfg["enabled"],
                                  key=f"chk_{mname}", help=defn["tip"])

            if checked != cfg["enabled"]:
                cfg["enabled"] = checked
                if not checked:
                    if mname in hlp: hlp.remove(mname)
                    if mname in rp:  rp.remove(mname)

            if cfg["enabled"]:
                mode_idx = {"highest":0,"lowest":1,"range":2}.get(cfg["mode"],0)
                mode_choice = st.radio("", ["Highest","Lowest","Range"],
                                       index=mode_idx, key=f"radio_{mname}",
                                       horizontal=True, label_visibility="collapsed")
                new_mode = mode_choice.lower()

                if new_mode != cfg["mode"]:
                    cfg["mode"] = new_mode
                    if new_mode == "range":
                        if mname in hlp: hlp.remove(mname)
                        if mname not in rp: rp.append(mname)
                    else:
                        if mname in rp: rp.remove(mname)
                        if mname not in hlp: hlp.append(mname)

                if cfg["mode"] == "range":
                    unit = "%" if defn["fmt"]=="pct" else "×"
                    col1, col2 = st.columns(2)
                    with col1:
                        cfg["range_min"] = st.number_input(
                            f"Min ({unit})",
                            value=float(cfg["range_min"]) if cfg["range_min"] is not None else 0.0,
                            step=1.0 if defn["fmt"]=="pct" else 0.5,
                            key=f"min_{mname}", label_visibility="visible")
                    with col2:
                        cfg["range_max"] = st.number_input(
                            f"Max ({unit})",
                            value=float(cfg["range_max"]) if cfg["range_max"] is not None else 50.0,
                            step=1.0 if defn["fmt"]=="pct" else 0.5,
                            key=f"max_{mname}", label_visibility="visible")
                else:
                    if mname not in hlp: hlp.append(mname)
                    if mname in rp: rp.remove(mname)

                # Pre-revenue toggle — only under the Revenue Growth metric.
                # Excluded by DEFAULT; check the box to include them.
                if mname == "Revenue Growth (YoY)":
                    st.checkbox(
                        "Include pre-revenue (rev < $50M)",
                        key="include_prerev",
                        help="Off by default: companies with under $50M revenue (current or "
                             "prior year) are hidden, because their growth % comes off a "
                             "near-zero base (e.g. Joby $0.1M→$53M = +39,000%) and distorts the "
                             "ranking. Check this to include them anyway.")

            st.markdown("<div style='margin-bottom:4px;'></div>", unsafe_allow_html=True)

        st.markdown("---")
        run_btn = st.button("🔍  Apply Filters", width='stretch')

    # ── NAVIGATION ─────────────────────────────────────────
    _nav_col1, _nav_col2, _ = st.columns([1,1,2])
    with _nav_col1:
        _pf_count = len(pf_load())
        _lbl = f"My Portfolio ({_pf_count})" if _pf_count else "My Portfolio"
        if st.button(_lbl, width='stretch', key="nav_portfolio"):
            st.session_state.page = "portfolio"; st.rerun()
    with _nav_col2:
        # Count upcoming earnings in next 14 days across universe
        from datetime import date as _date, timedelta as _td
        _today = _date.today()
        _earn_count = sum(
            1 for r in (st.session_state.universe_data or [])
            if r.get("next_earnings_date") and
            0 <= (_date(*map(int, r["next_earnings_date"][:10].split("-"))) - _today).days <= 14
        )
        _earn_lbl = f"📅 Earnings ({_earn_count})" if _earn_count else "📅 Earnings"
        if st.button(_earn_lbl, width='stretch', key="nav_earnings"):
            st.session_state.page = "earnings"; st.rerun()
    st.markdown("<br>", unsafe_allow_html=True)

    # ── HEADER ──────────────────────────────────────────────
    bg2=C["bg2"]; blue=C["blue"]; muted=C["muted"]; border=C["border"]; text=C["text"]; dim=C["dim"]
    st.markdown(f"<div style='font-family:IBM Plex Mono,monospace;font-size:22px;"
                f"font-weight:600;color:{blue};margin-bottom:4px;'>📈 Stock Screener</div>"
                f"<div style='font-size:12px;color:#ffffff;margin-bottom:20px;'>"
                f"Universe: {n_univ} companies · S&P 500 + NASDAQ 100 + market ({fm(UNIVERSE_MARKETCAP_FLOOR)}+)</div>", unsafe_allow_html=True)

    # ── SEARCH BAR ──────────────────────────────────────────
    search_q = st.text_input(
        "", placeholder="🔍  Search by ticker or company name (e.g. AAPL, Apple)…",
        key="company_search", label_visibility="collapsed")

    if search_q and univ:
        q = search_q.strip().upper()
        matches = [r for r in univ
                   if q in r["symbol"].upper() or q in r["name"].upper()][:10]
        if matches:
            st.markdown(f"<div style='font-size:13px;color:#ffffff;margin:8px 0 12px 0;'>"
                        f"{len(matches)} result{"s" if len(matches)>1 else ""} for "
                        f"<b>{search_q}</b></div>", unsafe_allow_html=True)
            for r in matches:
                st.markdown(build_search_card(r), unsafe_allow_html=True)
                _sd_col, _sp_col = st.columns([4,1])
                with _sd_col:
                    if st.button(f"📋 View Due Diligence — {r['symbol']}",
                                 key=f"srch_dd_{r['symbol']}", width='stretch'):
                        st.session_state.selected_symbol = r["symbol"]
                        st.session_state.page = "dd"; st.rerun()
                with _sp_col:
                    _sin_pf = pf_in_portfolio(r["symbol"])
                    if st.button("✅ In Portfolio" if _sin_pf else "+ Portfolio",
                                 key=f"spf_{r['symbol']}", width='stretch',
                                 help="Remove from portfolio" if _sin_pf else "Add to portfolio"):
                        if _sin_pf: pf_remove(r["symbol"])
                        else: pf_add(r["symbol"], univ)
                        st.rerun()
                st.markdown("<br>", unsafe_allow_html=True)
        else:
            st.info(f"No companies found matching '{search_q}'.")
        st.markdown("---")


    # ── HOW TO USE ──────────────────────────────────────────
    if not st.session_state.scan_done and not run_btn:
        st.markdown(
            f"<div style='background:{bg2};border:1px solid {border};"
            f"border-radius:10px;padding:28px 32px;max-width:600px;'>"
            f"<div style='font-family:IBM Plex Mono,monospace;color:{blue};font-size:20px;"
            f"font-weight:700;margin-bottom:16px;'>How to use</div>"
            f"<div style='color:#ffffff;font-size:16px;line-height:2.4;'>"
            f"1. Check a metric in the sidebar — choose <b style='color:{text};'>Highest</b>, "
            f"<b style='color:{text};'>Lowest</b>, or <b style='color:{text};'>Range</b><br>"
            f"2. The first Highest/Lowest you pick gets 🥇 — that's your primary sort<br>"
            f"3. Add a Range to hard-filter first, then sort within that range<br>"
            f"4. Hit <b style='color:{text};'>🔍 Apply Filters</b> — see every company that qualifies<br>"
            f"5. Click <b style='color:{text};'>View Due Diligence</b> on any card for the full deep dive"
            f"</div></div>", unsafe_allow_html=True)
        return

    # ── RUN FILTERS ─────────────────────────────────────────
    if run_btn:
        any_enabled = any(v["enabled"] for v in ms.values())
        if not any_enabled:
            st.warning("Enable at least one metric first."); return
        with st.spinner("Applying filters…"):
            results, sec_ranks = apply_metric_filters(univ, ms, hlp, rp)
        st.session_state.scan_results       = results
        st.session_state.secondary_rankings = sec_ranks
        st.session_state.scan_done          = True
        # Store metric labels for chips display
        hl_labels = [(n, ms[n]["mode"]) for n in hlp if ms[n]["enabled"]]
        rg_labels = [(n, ms[n]["range_min"], ms[n]["range_max"])
                     for n in rp if ms[n]["enabled"] and ms[n]["mode"]=="range"]
        st.session_state.selected_metrics = {
            "hl": hl_labels, "range": rg_labels
        }

    results   = st.session_state.scan_results or []
    sec_ranks = st.session_state.secondary_rankings or {}
    sel       = st.session_state.selected_metrics or {"hl":[],"range":[]}
    all_data  = univ

    if not results and st.session_state.scan_done:
        st.error("No companies matched your filters. Try widening the range or adjusting your criteria.")
        return
    if not results:
        return

    # ── KPI STRIP ───────────────────────────────────────────
    c1,c2,c3 = st.columns(3)
    for col,val,lbl in [
        (c1, str(len(results)),       "Companies qualified"),
        (c2, str(len(sel.get("hl",[])) + len(sel.get("range",[]))),"Filters applied"),
        (c3, results[0]["symbol"],    "🥇 Top Result"),
    ]:
        with col:
            st.markdown(f"<div style='background:{bg2};border:1px solid {border};"
                        f"border-radius:8px;padding:12px 16px;margin-bottom:12px;'>"
                        f"<div style='font-family:IBM Plex Mono,monospace;font-size:20px;"
                        f"font-weight:500;color:#ffffff;'>{val}</div>"
                        f"<div style='font-size:11px;color:#ffffff;margin-top:3px;'>{lbl}</div>"
                        f"</div>", unsafe_allow_html=True)

    # Filter chips
    chips = ""
    for n, mode in sel.get("hl",[]):
        badge_icon = BADGE.get(sel["hl"].index((n,mode))+1, "")
        chips += (f"<span style='display:inline-block;background:#1f3a5f;border:1px solid #388bfd;"
                  f"border-radius:6px;padding:3px 10px;font-size:12px;color:{blue};"
                  f"margin:3px;'>{badge_icon} {n} — {mode.capitalize()}</span>")
    for n, mn, mx in sel.get("range",[]):
        unit = "%" if METRIC_DEFS[n]["fmt"]=="pct" else "×"
        chips += (f"<span style='display:inline-block;background:#1a3a2a;border:1px solid #3fb950;"
                  f"border-radius:6px;padding:3px 10px;font-size:12px;color:#3fb950;"
                  f"margin:3px;'>{n} {mn}{unit}–{mx}{unit}</span>")
    if chips:
        st.markdown(f"<div style='margin-bottom:20px;'>{chips}</div>", unsafe_allow_html=True)

    # ── RESULT CARDS ────────────────────────────────────────
    top_n = min(10, len(results))
    header_txt = (f"Top {top_n} of {len(results)} Results"
                  if len(results) > 10 else f"All {len(results)} Results")
    st.markdown(f"<div style='font-size:14px;font-weight:600;color:{text};"
                f"margin-bottom:16px;'>{header_txt}</div>", unsafe_allow_html=True)

    col_a, col_b = st.columns(2)
    for i, r in enumerate(results[:10]):
        col = col_a if i%2==0 else col_b
        with col:
            # Build selected metric list for hero tiles
            active_names = [n for n,_ in sel.get("hl",[])] + [n for n,_,_ in sel.get("range",[])]
            st.markdown(main_card_html(r, i+1, active_names, all_data,
                                       sec_ranks, len(results)),
                        unsafe_allow_html=True)
            _dd_col, _pf_col = st.columns([4,1])
            with _dd_col:
                if st.button(f"📋 View Due Diligence — {r['symbol']}",
                             key=f"dd_{r['symbol']}", width='stretch'):
                    st.session_state.selected_symbol = r["symbol"]
                    st.session_state.page = "dd"; st.rerun()
            with _pf_col:
                _in_pf = pf_in_portfolio(r["symbol"])
                _lbl_pf = "✅ In Portfolio" if _in_pf else "+ Portfolio"
                if st.button(_lbl_pf, key=f"pf_{r['symbol']}", width='stretch',
                             help="Remove from portfolio" if _in_pf else "Add to portfolio"):
                    if _in_pf: pf_remove(r["symbol"])
                    else: pf_add(r["symbol"], univ)
                    st.rerun()
            st.markdown("<br>", unsafe_allow_html=True)

    # ── FULL TABLE ──────────────────────────────────────────
    if results:
        st.markdown("---")
        st.markdown(f"<div style='font-size:13px;font-weight:600;color:{text};"
                    f"margin-bottom:10px;'>All Qualifying Companies</div>", unsafe_allow_html=True)
        disp = []
        for idx, r in enumerate(results):
            row = {"Rank": idx+1, "Symbol":r["symbol"], "Company":r["name"],
                   "Sector":r["sector"], "Mkt Cap":r["mkt_cap"]}
            for n,_ in sel.get("hl",[]):
                f2=METRIC_DEFS[n]["field"]; v=r.get(f2)
                row[n] = fp(v) if METRIC_DEFS[n]["fmt"]=="pct" else fx(v)
            for n,_,_ in sel.get("range",[]):
                f2=METRIC_DEFS[n]["field"]; v=r.get(f2)
                row[n] = fp(v) if METRIC_DEFS[n]["fmt"]=="pct" else fx(v)
            # Secondary rankings
            for sec_name, rank_field in sec_ranks.items():
                row[f"{sec_name} Rank"] = f"#{int(r.get(rank_field,0))}" if r.get(rank_field) else "—"
            disp.append(row)
        st.dataframe(pd.DataFrame(disp), width='stretch', hide_index=True)
        st.download_button("⬇ Download CSV",
                           pd.DataFrame(disp).to_csv(index=False).encode(),
                           "screener_results.csv","text/csv")

# ─────────────────────────────────────────────────────────────
# EARNINGS CALENDAR PAGE
# ─────────────────────────────────────────────────────────────
def show_earnings_page():
    from datetime import date as _date, timedelta
    bg2=C["bg2"]; blue=C["blue"]; text=C["text"]; dim=C["dim"]
    border=C["border"]; amber=C["amber"]; green=C["green"]; muted=C["muted"]; red=C["red"]

    # Nav
    nav1, nav2, nav3 = st.columns([1,1,1])
    with nav1:
        if st.button("📈 Screener", width='stretch', key="earn_nav_screen"):
            st.session_state.page = "screener"; st.rerun()
    with nav2:
        _pf_count = len(pf_load())
        _lbl = f"My Portfolio ({_pf_count})" if _pf_count else "My Portfolio"
        if st.button(_lbl, width='stretch', key="earn_nav_pf"):
            st.session_state.page = "portfolio"; st.rerun()
    with nav3:
        st.button("📅 Earnings Calendar", width='stretch', disabled=True)
    st.markdown("<br>", unsafe_allow_html=True)

    st.markdown(
        f"<div style='font-family:IBM Plex Mono,monospace;font-size:22px;"
        f"font-weight:600;color:{blue};margin-bottom:4px;'>📅 Earnings Calendar</div>"
        f"<div style='font-size:13px;color:#ffffff;margin-bottom:20px;'>"
        f"Upcoming earnings releases across your loaded universe</div>",
        unsafe_allow_html=True)

    univ = st.session_state.universe_data or []
    today = _date.today()
    cutoff = today + timedelta(days=14)

    # Split into: this week (≤7d), next week (8–14d), portfolio companies
    pf_syms = {c["symbol"] for c in pf_load()}

    upcoming = []
    for r in univ:
        ned = r.get("next_earnings_date","")
        if not ned: continue
        try:
            ned_dt = datetime.strptime(ned[:10], "%Y-%m-%d").date()
        except: continue
        if ned_dt < today or ned_dt > cutoff: continue
        days_out = (ned_dt - today).days
        upcoming.append({**r, "_ned_dt": ned_dt, "_days_out": days_out})

    upcoming.sort(key=lambda x: x["_ned_dt"])

    if not upcoming:
        st.markdown(
            f"<div style='background:{bg2};border:1px solid {border};"
            f"border-radius:10px;padding:24px;text-align:center;color:{muted};'>"
            f"No earnings releases found in the next 14 days for your loaded universe.</div>",
            unsafe_allow_html=True)
    else:
        # KPI strip
        k1, k2, k3 = st.columns(3)
        week1 = [x for x in upcoming if x["_days_out"] <= 7]
        week2 = [x for x in upcoming if x["_days_out"] > 7]
        pf_upcoming = [x for x in upcoming if x["symbol"] in pf_syms]
        for col, val, lbl, color in [
            (k1, str(len(upcoming)),    "Reporting in 14 days", text),
            (k2, str(len(week1)),       "Reporting this week",  amber if week1 else dim),
            (k3, str(len(pf_upcoming)), "In your portfolio",    green if pf_upcoming else dim),
        ]:
            with col:
                st.markdown(
                    f"<div style='background:{bg2};border:1px solid {border};"
                    f"border-radius:8px;padding:12px 16px;margin-bottom:16px;'>"
                    f"<div style='font-family:IBM Plex Mono,monospace;font-size:24px;"
                    f"font-weight:600;color:{color};'>{val}</div>"
                    f"<div style='font-size:11px;color:{dim};margin-top:3px;'>{lbl}</div>"
                    f"</div>", unsafe_allow_html=True)

        def earn_card(r):
            days   = r["_days_out"]
            ned    = r.get("next_earnings_date","")[:10]
            ntime  = r.get("next_earnings_time","")
            neps   = r.get("next_earnings_eps_est")
            time_str = {"amc":"After Market Close","bmo":"Before Market Open"}.get(ntime, ntime or "TBD")
            eps_str  = f"EPS Est: ${neps:.2f}" if neps else "EPS Est: TBD"
            in_pf    = r["symbol"] in pf_syms

            if days == 0:   urgency = ("🔴 TODAY",    red)
            elif days <= 2: urgency = (f"🟠 In {days}d", amber)
            elif days <= 7: urgency = (f"🟡 In {days}d", amber)
            else:           urgency = (f"🟢 In {days}d", green)

            pf_badge = (f"<span style='font-size:10px;padding:2px 7px;border-radius:4px;"
                        f"background:#162520;color:{green};margin-left:8px;'>Portfolio</span>"
                        if in_pf else "")

            return (
                f"<div style='background:{bg2};border:1px solid {border};"
                f"border-radius:10px;padding:14px 18px;margin-bottom:8px;"
                f"display:flex;justify-content:space-between;align-items:center;'>"
                f"<div>"
                f"<div style='display:flex;align-items:center;'>"
                f"<span style='font-family:IBM Plex Mono,monospace;font-size:16px;"
                f"font-weight:600;color:{text};'>{r['symbol']}</span>{pf_badge}</div>"
                f"<div style='font-size:12px;color:{dim};margin-top:2px;'>{r.get('name','')}</div>"
                f"<div style='font-size:11px;color:{muted};'>{r.get('sector','—')}</div>"
                f"</div>"
                f"<div style='text-align:right;'>"
                f"<div style='font-family:IBM Plex Mono,monospace;font-size:14px;"
                f"font-weight:600;color:{urgency[1]};'>{urgency[0]}</div>"
                f"<div style='font-size:12px;color:{dim};margin-top:3px;'>{ned}</div>"
                f"<div style='font-size:11px;color:{muted};'>{time_str}</div>"
                f"<div style='font-size:11px;color:{muted};'>{eps_str}</div>"
                f"</div></div>"
            )

        if week1:
            st.markdown(
                f"<div style='font-size:12px;font-weight:700;color:{amber};"
                f"text-transform:uppercase;letter-spacing:1px;margin-bottom:10px;'>"
                f"⚠️ This Week ({len(week1)} companies)</div>", unsafe_allow_html=True)
            for r in week1:
                st.markdown(earn_card(r), unsafe_allow_html=True)
                if st.button(f"📋 View DD — {r['symbol']}", key=f"earn_dd_{r['symbol']}"):
                    st.session_state.selected_symbol = r["symbol"]
                    st.session_state.page = "dd"; st.rerun()

        if week2:
            st.markdown(
                f"<div style='font-size:12px;font-weight:700;color:{green};"
                f"text-transform:uppercase;letter-spacing:1px;"
                f"margin-top:20px;margin-bottom:10px;'>"
                f"📆 Next Week ({len(week2)} companies)</div>", unsafe_allow_html=True)
            for r in week2:
                st.markdown(earn_card(r), unsafe_allow_html=True)
                if st.button(f"📋 View DD — {r['symbol']}", key=f"earn_dd2_{r['symbol']}"):
                    st.session_state.selected_symbol = r["symbol"]
                    st.session_state.page = "dd"; st.rerun()

# ─────────────────────────────────────────────────────────────
# AUTH GATE — per-user login (secrets-driven). No [auth] config → local mode.
# ─────────────────────────────────────────────────────────────
import hashlib

def _pw_hash(pw):
    """Demo-grade password hash (PBKDF2). Store the hex output in secrets."""
    return hashlib.pbkdf2_hmac("sha256", str(pw).encode(), b"stkscreener_v1", 120000).hex()

def require_login():
    """Gate access. If [auth].users is configured, require a valid login and set
    `pf_user` to that username (which scopes their portfolio). If not configured,
    allow in as 'local' (single-user dev mode). Returns True when access granted."""
    try:
        users = dict(st.secrets["auth"]["users"]) if "auth" in st.secrets else None
    except Exception:
        users = None
    if not users:                                   # local dev — no auth configured
        st.session_state["pf_user"] = "local"
        return True

    if st.session_state.get("authed"):
        with st.sidebar:
            st.caption(f"Signed in as **{st.session_state.get('pf_user','')}**")
            if st.button("Log out", key="logout_btn"):
                for k in ("authed", "pf_user", "pf_cache", "pf_cache_user"):
                    st.session_state.pop(k, None)
                st.rerun()
        return True

    # ── login screen ──
    st.markdown(
        f"<div style='max-width:420px;margin:80px auto 0 auto;text-align:center;'>"
        f"<div style='font-family:IBM Plex Mono,monospace;font-size:28px;font-weight:600;"
        f"color:{C['blue']};margin-bottom:6px;'>📈 Stock Screener</div>"
        f"<div style='font-size:14px;color:{C['dim']};margin-bottom:24px;'>"
        f"Please sign in to continue</div></div>", unsafe_allow_html=True)
    _c1, _c2, _c3 = st.columns([1, 2, 1])
    with _c2:
        with st.form("login_form"):
            u = st.text_input("Username")
            p = st.text_input("Password", type="password")
            ok = st.form_submit_button("Log in", width='stretch')
        if ok:
            uname = (u or "").strip().lower()
            if uname in users and _pw_hash(p) == str(users[uname]):
                st.session_state["authed"]  = True
                st.session_state["pf_user"] = uname
                st.session_state.pop("pf_cache", None)
                st.rerun()
            else:
                st.error("Incorrect username or password.")
    return False

# ─────────────────────────────────────────────────────────────
# ROUTING
# ─────────────────────────────────────────────────────────────
if not require_login():
    st.stop()
elif not st.session_state.universe_loaded:
    show_loading_page()
elif st.session_state.page == "dd" and st.session_state.selected_symbol:
    show_dd_page()
elif st.session_state.page == "portfolio":
    show_portfolio_page()
elif st.session_state.page == "earnings":
    show_earnings_page()
else:
    show_screener_page()