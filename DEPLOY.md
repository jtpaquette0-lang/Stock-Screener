# Deploying the Stock Screener

This app is a Streamlit web app. Hosting it means: push the code to GitHub →
connect Streamlit Community Cloud → paste in your secrets. Friends then use it in
any browser via a link. No installs, no Claude, no VS Code needed on their end.

**What you'll set up (all free):**
1. A **GitHub** repo (private is fine)
2. A **Supabase** project (stores each user's saved portfolio)
3. A **Streamlit Community Cloud** app (hosts the site)
4. A **GitHub Action** that rebuilds the data cache every morning (already in the repo)

---

## 0. Before you push — secrets check ✅ (already done)

- `.gitignore` excludes `.streamlit/secrets.toml` (your keys never hit GitHub)
- The hardcoded key was removed from `test_fmp_upgrade.py`
- `universe_cache.json` **is** committed on purpose (so the hosted app loads instantly)

Double-check nothing secret is staged before your first push:
```bash
git status          # secrets.toml should NOT appear
```

---

## 1. Push to GitHub

```bash
git init
git add .
git commit -m "Stock screener"
# create an empty repo on github.com first, then:
git remote add origin https://github.com/YOU/stock-screener.git
git branch -M main
git push -u origin main
```

---

## 2. Supabase (per-user portfolios)

1. Go to **supabase.com** → **New project** (free tier). Pick a region near you.
2. Once it's ready, open the **SQL Editor** and run:

   ```sql
   create table portfolios (
     username   text primary key,
     companies  jsonb default '[]'::jsonb,
     updated_at timestamptz default now()
   );

   -- Demo-grade: the app is the only client and the key stays in Streamlit
   -- secrets, so we let the app read/write directly. For a real product,
   -- replace this with proper Row-Level-Security policies.
   alter table portfolios disable row level security;
   ```
3. Go to **Project Settings → API** and copy:
   - **Project URL**  → this is `SUPABASE_URL`
   - **anon public key** → this is `SUPABASE_KEY`

> If Supabase isn't configured, the app still runs — it just falls back to a local
> `portfolio.json` (single user). So you can test everything locally first.

---

## 3. Create user logins

Each friend gets a username + password. Generate the password **hash** for each
(never store the raw password):

```bash
python -c "import hashlib; print(hashlib.pbkdf2_hmac('sha256', b'THEIR_PASSWORD', b'stkscreener_v1', 120000).hex())"
```

Run it once per user, swapping in their password. You'll paste the hashes into
the secrets in the next step.

---

## 4. Deploy on Streamlit Community Cloud

1. Go to **share.streamlit.io** → **New app** → pick your GitHub repo, branch
   `main`, main file `app.py`.
2. Open **Advanced settings → Secrets** and paste (TOML format):

   ```toml
   FMP_API_KEY       = "your_fmp_key"
   ANTHROPIC_API_KEY = "your_anthropic_key"
   SUPABASE_URL      = "https://xxxx.supabase.co"
   SUPABASE_KEY      = "eyJhbGciOi...your_anon_key..."

   [auth.users]
   jack    = "paste_jack_password_hash_here"
   friend1 = "paste_friend1_password_hash_here"
   ```
3. **Deploy.** First load reads the committed `universe_cache.json` instantly —
   no 25-minute wait on the server.
4. Share the `https://your-app.streamlit.app` link. Friends log in with the
   username/password you set. Each sees only their own saved portfolio.

> **No `[auth.users]` section?** The app becomes open (no login) — fine for a
> quick private demo, but add users before sharing the link publicly.

---

## 5. Automated daily data refresh (already wired)

`.github/workflows/refresh.yml` rebuilds `universe_cache.json` every morning
(10 AM Eastern; 15:00 UTC) and commits it; Streamlit Cloud auto-redeploys with
fresh data. GitHub cron is UTC-only and doesn't observe DST, so during summer
(Daylight Saving) it fires at 11 AM Eastern — change `15` to `14` in the cron if
you'd rather it stay 10 AM during summer.

**One thing to enable it:** add the FMP key as a GitHub secret so the Action can
fetch data:

- GitHub repo → **Settings → Secrets and variables → Actions → New repository
  secret** → name `FMP_API_KEY`, value = your key.

You can also trigger it manually anytime: repo → **Actions → Daily universe
cache refresh → Run workflow**.

> The rebuild takes ~25 min of GitHub Actions time/day (~750 min/month). Public
> repos: unlimited. Private repos: free tier is 2,000 min/month, so you're fine.

---

## Running locally (for development)

```powershell
python -m streamlit run app.py
```
With no secrets configured it runs in single-user mode against the local cache
and `portfolio.json`. Add a `.streamlit/secrets.toml` (git-ignored) to test the
hosted config locally.

---

## Notes & limits

- **Costs are on your keys.** Loading the universe uses the committed cache
  (near-zero FMP calls). Costs accrue only when someone opens a Due-Diligence
  page (a few FMP calls + one Anthropic legal-summary call). The login keeps
  strangers out.
- **Auth is demo-grade** (PBKDF2 hash, session-based login — a browser refresh
  asks for login again). Fine for a friend group; for a real product, move to
  Supabase Auth or a dedicated auth provider.
- To add a friend later: generate their hash (step 3), add a line under
  `[auth.users]` in Streamlit secrets, save. No redeploy needed.
