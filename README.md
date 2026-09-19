# Job Outreach Pipeline

Python pipeline for job seekers: find LinkedIn hiring posts → extract recruiter emails →
score against your resume → draft personalized emails → human review → send via Gmail.

## Stages

1. `ingest` — LinkedIn feed, LinkedIn DMs (bookmarks/inbound), and/or Apify (pluggable `Source`)
2. `parse` — regex emails + LLM structured fields
3. `enrich` — optional Hunter/Apollo lookup (`enrich.enabled`)
4. `match` — resume score 0–100
5. `compose` — personalized draft → `pending_review`
6. `send` — approval queue + throttled Gmail

## LinkedIn personal feed (recommended)

Your connections' hiring posts often beat keyword search.

```powershell
uv sync --extra dev
uv run playwright install chromium
uv run pipeline linkedin-login    # log in once in the browser window
```

`config.yaml` already has `ingest.source: linkedin_feed`. Then:

```powershell
uv run pipeline ingest            # scrolls your Home feed, keeps hiring posts
uv run pipeline parse
uv run pipeline match
uv run pipeline compose
uv run pipeline review
uv run pipeline send
```

Session file: `credentials/linkedin_storage_state.json` (gitignored).  
If LinkedIn asks you to log in again: re-run `linkedin-login`.

**Risk:** automating LinkedIn can restrict accounts — keep `max_posts_per_run` low and don't run 24/7.

### LinkedIn Messaging (DM bookmarks + inbound)

Use Messaging as a second source: openings friends send you, or jobs you forward to a bookmark contact (default: **Abhishek Kumar**).

Opening a chat during scrape is **read-only** — it never marks a post as applied. Only approve → Gmail send does.

In `config.yaml`:

```yaml
ingest:
  source: linkedin_dms
  dm:
    bookmark_contacts:
      - "Abhishek Kumar"
    inbound_contacts: []          # optional friend names
    scan_recent_if_no_inbound: true
```

```powershell
uv run pipeline linkedin-login    # if session expired
uv run pipeline ingest            # opens Messaging, scrapes watchlist threads
```

Switch back to the feed with `ingest.source: linkedin_feed`. Multi-source in one run is not built yet — run twice with different `source` values.

To switch to Apify keyword search: set `ingest.source: apify`.

## Setup

```powershell
$env:Path = "$env:USERPROFILE\.local\bin;" + $env:Path
cd D:\web-scrapping\web-scrapping
uv sync --extra dev
copy .env.example .env
```

Fill `.env`:

- `OPENAI_API_KEY` (or Anthropic)
- `APIFY_API_TOKEN`
- `GMAIL_SENDER` (your address)
- Optional: `HUNTER_API_KEY` / `APOLLO_API_KEY` for enrich

Put OAuth Desktop client JSON at `credentials/gmail_client_secrets.json`.

Put your resume at `resume/resume.txt` or `resume/resume.pdf` (see `config.yaml`).

Edit `config.yaml` — especially `ingest.target_roles` and `match.score_cutoff`.

## Commands

```powershell
uv run pipeline --help
uv run pipeline status
uv run pipeline run --dry-run
uv run pipeline ingest
uv run pipeline parse
uv run pipeline enrich          # only if enrich.enabled=true
uv run pipeline match
uv run pipeline compose
uv run pipeline review          # a/e/r/b/s/q on each draft
uv run pipeline send --dry-run
uv run pipeline send            # approved only; business hours + throttle
```

## Full dry-run walkthrough

1. **Preview ingest** (no Apify spend):
   ```powershell
   uv run pipeline ingest --dry-run
   ```
2. **Live ingest** (uses Apify credits):
   ```powershell
   uv run pipeline ingest
   uv run pipeline status
   ```
3. **Parse**:
   ```powershell
   uv run pipeline parse --dry-run
   uv run pipeline parse
   ```
4. **Match + compose** (needs resume file):
   ```powershell
   uv run pipeline match
   uv run pipeline compose
   ```
5. **Review** (required while `require_approval: true`):
   ```powershell
   uv run pipeline review
   ```
   Keys: `a` approve · `e` edit · `r` reject · `b` reject+blacklist company · `s` skip · `q` quit
6. **Send dry-run**, then live (weekdays, business hours, max 25/day, 45–180s gaps):
   ```powershell
   uv run pipeline send --dry-run
   uv run pipeline send
   ```

First live Gmail send opens a browser for OAuth; token is saved to `credentials/gmail_token.json`.

## Safety

- Secrets only in `.env` (never commit)
- `require_approval: true` by default
- Never emails the same address twice within 60 days (`recipient_cooldowns` UNIQUE)
- Enrich never invents `first.last@domain` patterns — low-confidence results are discarded

## Tests

```powershell
uv run pytest -q
```
