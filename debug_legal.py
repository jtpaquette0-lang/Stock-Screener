"""
Run this in your jackMac folder:
    py debug_legal.py
Then paste the full output back to Claude.
"""
import requests, re, html as _html

headers = {"User-Agent": "StockScreener research@stockscreener.com"}
CIK = "0000886982"  # Goldman Sachs
CIK_INT = 886982
SYM = "GS"

def strip_ix(href):
    return href.split("ix?doc=")[-1] if "ix?doc=" in href else href

def clean_html_text(raw):
    text = re.sub(r'<style[^>]*>.*?</style>', ' ', raw, flags=re.DOTALL|re.IGNORECASE)
    text = re.sub(r'<script[^>]*>.*?</script>', ' ', text, flags=re.DOTALL|re.IGNORECASE)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = _html.unescape(text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n', text)
    return text.strip()

print("=" * 60)
print("STEP 1: Fetch EDGAR submissions for GS")
print("=" * 60)
r = requests.get(f"https://data.sec.gov/submissions/CIK{CIK}.json",
                 headers=headers, timeout=15)
print(f"Status: {r.status_code}")
if r.status_code != 200:
    print("FAILED - cannot continue")
    exit()

subs = r.json()
filings = subs.get("filings", {}).get("recent", {})
forms   = filings.get("form", [])
accnums = filings.get("accessionNumber", [])
docs    = filings.get("primaryDocument", [])
dates   = filings.get("filingDate", [])

tenk_idx = next((i for i, f in enumerate(forms) if f == "10-K"), None)
print(f"10-K index: {tenk_idx}")
if tenk_idx is None:
    print("No 10-K found")
    exit()

acc_raw   = accnums[tenk_idx]
acc_nodash = acc_raw.replace("-","")
primary   = docs[tenk_idx]
date      = dates[tenk_idx]
print(f"10-K date: {date}")
print(f"Accession: {acc_raw}")
print(f"Primary doc: {primary}")

print("\n" + "=" * 60)
print("STEP 2: Fetch filing index")
print("=" * 60)
index_url = f"https://www.sec.gov/Archives/edgar/data/{CIK_INT}/{acc_nodash}/{acc_raw}-index.htm"
print(f"Index URL: {index_url}")
ri = requests.get(index_url, headers=headers, timeout=15)
print(f"Index status: {ri.status_code}, len: {len(ri.text)}")

doc_urls = []
if ri.status_code == 200:
    hrefs = re.findall(r'href="([^"]+)"', ri.text, re.IGNORECASE)
    print(f"All hrefs found: {len(hrefs)}")
    for href in hrefs:
        orig = href
        href = strip_ix(href)
        hl = href.lower()
        if hl.endswith((".htm",".html")):
            full = (f"https://www.sec.gov{href}" if href.startswith("/")
                    else f"https://www.sec.gov/Archives/edgar/data/{CIK_INT}/{acc_nodash}/{href}")
            is_exhibit = any(x in hl for x in ["ex-","exhibit","ex99","xsd","cal","def","lab","pre"])
            score = (4 if SYM.lower() in hl else 0) + (3 if "10-k" in hl else 0)
            print(f"  {'[EXHIBIT]' if is_exhibit else '[DOC]    '} score={score} | {href[:80]}")
            if not is_exhibit:
                doc_urls.append((score, full))
    doc_urls.sort(reverse=True)

# Also add primary
doc_urls.append((0, f"https://www.sec.gov/Archives/edgar/data/{CIK_INT}/{acc_nodash}/{primary}"))

print("\n" + "=" * 60)
print("STEP 3: Try fetching each document")
print("=" * 60)
for score, url in doc_urls[:5]:
    print(f"\nTrying (score={score}): {url[:100]}")
    try:
        r2 = requests.get(url, headers=headers, timeout=30)
        print(f"  Status: {r2.status_code}, len: {len(r2.text)}")
        if r2.status_code == 200:
            # Check if it's the XBRL viewer wrapper
            if "ixvFrame" in r2.text:
                print("  -> XBRL VIEWER WRAPPER (not real doc)")
                continue
            if "XBRL Viewer" in r2.text:
                print("  -> XBRL VIEWER PAGE")
                continue
            # Clean and check for legal content
            text = clean_html_text(r2.text)
            print(f"  Cleaned text len: {len(text)}")
            cl = text.lower()
            lp_idx = cl.find("legal proceedings")
            print(f"  'legal proceedings' found at index: {lp_idx}")
            if lp_idx > 0:
                print(f"  Context: {text[lp_idx:lp_idx+300]}")
            cont_idx = cl.find("contingenc")
            print(f"  'contingenc' found at index: {cont_idx}")
            if cont_idx > 0:
                print(f"  Context: {text[cont_idx:cont_idx+300]}")
            # Show first 500 chars
            print(f"  First 500 chars: {text[:500]}")
    except Exception as e:
        print(f"  ERROR: {e}")

print("\n" + "=" * 60)
print("DONE - paste all output above back to Claude")
print("=" * 60)
