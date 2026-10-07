# Command Cheat Sheet

All commands run from `D:\web-scrapping\web-scrapping`.

If `uv` is not recognized in a new terminal:

```powershell
$env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
cd D:\web-scrapping\web-scrapping
```

Common flags (work on almost every command):

| Flag | Meaning |
|---|---|
| `--dry-run` | Show what would happen — no browser, no API calls, no DB writes, no sends |
| `-v` / `--verbose` | Debug logging |
| `--config path.yaml` | Use a different config file |

---

## 1. One-time setup

```powershell
uv run pipeline init-db            # create SQLite tables (also runs automatically)
uv run pipeline linkedin-login     # log into LinkedIn once; saves session for feed/DMs/connections
uv run pipeline naukri-login       # optional: Naukri session for job discovery
```

Gmail login happens automatically the first time you run `send` (browser opens).
If the token expires, `send` re-opens the Google sign-in page by itself.

---

## 2. Email outreach pipeline (job posts → emails)

Flow: **ingest → parse → match → compose → review → send**

### Ingest (collect posts)

```powershell
uv run pipeline ingest -s feed     # scroll your LinkedIn home feed (until 5 new emails / 10 min)
uv run pipeline ingest -s dms      # friends' DMs + bookmark chat (Abhishek Kumar)
uv run pipeline ingest -s posts    # LinkedIn post search via Apify (7 queries)
uv run pipeline ingest -s naukri   # Naukri discovery (never auto-applies)
uv run pipeline ingest -s all      # feed + dms + posts
```

`-s` aliases: `feed` = `linkedin_feed`, `dms`/`dm` = `linkedin_dms`, `posts` = `apify`.
Without `-s`, the default `ingest.source` from `config.yaml` is used.

### Process

```powershell
uv run pipeline parse              # LLM extracts role, company, email, experience
uv run pipeline enrich             # find emails for posts without one (only if enrich.enabled)
uv run pipeline match              # score against your resume (cutoff 60, 1-4 yrs not penalised)
uv run pipeline match --rescore-rejected   # also re-check recent rejects within the experience window
uv run pipeline compose            # draft emails for matched posts
```

### Review and send

```powershell
uv run pipeline review             # approve / edit / reject drafts
uv run pipeline send               # send approved drafts via Gmail (business hours, delays)
uv run pipeline send --force       # ignore business hours (testing)
```

Review keys (email drafts): `a` approve · `e` edit · `r` reject · `b` reject + blacklist company · `s` skip · `q` quit

### Everything in one go

```powershell
uv run pipeline run -s all         # ingest → parse → enrich → match → compose → send
uv run pipeline run -s feed --skip-enrich
```

Note: `run` does not include `review` — only already-approved drafts get sent.
Typical daily routine:

```powershell
uv run pipeline ingest -s all
uv run pipeline parse
uv run pipeline match
uv run pipeline compose
uv run pipeline review
uv run pipeline send
```

---

## 3. Naukri (discover + shortlist, manual apply)

```powershell
uv run pipeline naukri-login                 # once
uv run pipeline ingest -s naukri             # collect matching jobs (no Apply clicks)
uv run pipeline parse
uv run pipeline match
uv run pipeline shortlist                    # writes data/naukri_shortlist.md
uv run pipeline shortlist -s all             # shortlist from every source
uv run pipeline shortlist --min-score 50 --limit 100
```

Open `data/naukri_shortlist.md` and apply manually via the links.

---

## 4. LinkedIn connection outreach (message HR / leaders / seniors)

Flow: **connections-sync → connections-review → connections-send**

```powershell
uv run pipeline connections-sync             # scrape connections, verify profiles, draft messages
uv run pipeline connections-review           # approve / edit / reject each message
uv run pipeline connections-send             # send approved (15/day, 2-5 min apart, business hours)
uv run pipeline connections-send --dry-run   # preview who would be messaged
uv run pipeline connections-send --force     # ignore business hours (testing)
```

Review keys (LinkedIn messages): `a` approve · `e` edit · `r` reject (never message) · `s` skip (decide later) · `q` quit

Rules: HangingPanda employees excluded · HR/leaders qualify by title · others need 4+ years ·
nobody is messaged twice · existing chats skipped · stops on any LinkedIn warning.

---

## 4b. LinkedIn connection requests (grow network in Noida / Delhi / Gurugram)

Flow: **prospects-search → prospects-review → prospects-invite** → (they accept) → section 4

```powershell
uv run pipeline prospects-search             # people search: HR at small/mid companies (no MNCs) x Noida/Delhi/Gurgaon, 2nd degree
uv run pipeline prospects-review             # approve / reject; A = approve all remaining
uv run pipeline prospects-invite             # connection requests, no note (15/day, 80/week, 1-3 min apart)
uv run pipeline prospects-invite --dry-run   # preview who would be invited
uv run pipeline prospects-stats              # invited / accepted / acceptance rate / budget left
```

Review keys (prospects): `a` approve · `r` reject · `s` skip · `A` approve this + all remaining · `q` quit

After people accept, run `connections-sync` → they get the "we just connected" message via
`connections-review` / `connections-send`. Keep acceptance rate above ~25% (`prospects-stats`).
Each search run covers 6 of the 30 title x city searches; the rotation changes daily.

---

## 5. Status and debugging

```powershell
uv run pipeline status             # post counts by status
uv run pipeline --help             # list all commands
uv run pipeline <command> --help   # options for one command
uv run pytest -q                   # run tests
```

Debug artifacts: `data/debug/linkedin_feed_last.png` and `.html` (saved when a scrape fails or LinkedIn blocks).

---

## 6. Where to change behaviour (`config.yaml`)

| Want to change | Key |
|---|---|
| Default ingest source | `ingest.source` |
| Roles / keywords for feed + DM filter | `ingest.target_roles` |
| Feed: emails goal / time limit | `ingest.feed_min_with_email`, `ingest.feed_max_minutes` |
| Post search queries | `ingest.post_search_queries` |
| DM friends / bookmark chat | `ingest.dm.inbound_contacts`, `ingest.dm.bookmark_contacts` |
| Naukri keywords / locations | `ingest.naukri.*` |
| Match strictness | `match.score_cutoff`, `match.experience_flex_max_years` |
| Email daily cap / delays / hours | `send.max_sends_per_day`, `send.delay_seconds_*`, `send.business_hours` |
| Email re-contact window | `send.email_cooldown_days` |
| Hunter email lookup | `enrich.enabled` (+ `HUNTER_API_KEY` in `.env`) |
| LinkedIn message cap / delays / templates | `network.daily_cap`, `network.delay_seconds_*`, `network.template_*` |
| Companies never to message / invite | `network.exclude_companies` |
| Invite caps / delays | `network.prospects.daily_cap`, `weekly_cap`, `delay_seconds_*` |
| Invite targets (titles, cities, degree) | `network.prospects.titles`, `locations`, `network` |
| LLM provider order / free fallback | `.env`: `LLM_PROVIDERS=openai,groq,gemini`, `GROQ_API_KEY`, `GEMINI_API_KEY`, `GROQ_MODELS`, `GEMINI_MODELS` |
uv run pipeline ingest -s all
uv run pipeline parse
uv run pipeline match
uv run pipeline compose
uv run pipeline review
uv run pipeline send
