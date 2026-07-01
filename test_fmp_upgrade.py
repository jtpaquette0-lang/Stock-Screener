import os
import requests

# Read the key from the environment — never hardcode secrets.
#   PowerShell:  $env:FMP_API_KEY="your_key";  python test_fmp_upgrade.py
API_KEY = os.environ.get("FMP_API_KEY", "")
BASE    = "https://financialmodelingprep.com/stable"
if not API_KEY:
    raise SystemExit("Set FMP_API_KEY in your environment first.")

def test(label, url):
    try:
        r = requests.get(url, timeout=15)
        data = r.json()
        if isinstance(data, list):
            print(f"  ✅ {label}: {len(data)} companies — first: {data[0].get('symbol','?') if data else 'empty'}")
        elif isinstance(data, dict):
            print(f"  ⚠️  {label}: dict — {str(data)[:100]}")
        else:
            print(f"  ❌ {label}: unexpected type")
    except Exception as e:
        print(f"  ❌ {label}: {e}")

print("\n── Constituent List Endpoints ───────────────────────────")
test("sp500-constituent",     f"{BASE}/sp500-constituent?apikey={API_KEY}")
test("sp400-constituent",     f"{BASE}/sp400-constituent?apikey={API_KEY}")
test("sp600-constituent",     f"{BASE}/sp600-constituent?apikey={API_KEY}")
test("nasdaq-constituent",    f"{BASE}/nasdaq-constituent?apikey={API_KEY}")
test("dowjones-constituent",  f"{BASE}/dowjones-constituent?apikey={API_KEY}")
test("stock-screener (500)",  f"{BASE}/stock-screener?marketCapMoreThan=10000000000&limit=500&apikey={API_KEY}")
test("available-traded-list", f"{BASE}/available-traded-list?apikey={API_KEY}")

print("\nDone.\n")
