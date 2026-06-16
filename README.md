# JobPilot — Autopilot for Job Applications

An open-source, self-hosted job-search pipeline that **discovers**, **scores**, and **auto-applies** to early-career software engineering roles — all from your own machine, with your own data.

```
discover → normalize → gate → dedupe → score → review → apply → sync → report
```

---

## Why JobPilot?

Job searching at scale is repetitive, error-prone, and time-consuming. JobPilot turns that process into a structured, auditable pipeline:

- Pulls from **multiple job boards and ATS APIs** in one run
- Filters by recency, seniority, location, and tech stack match
- Scores each role against your candidate profile using an **LLM rubric**
- Stops for **human review** before touching any submit button
- Auto-fills and submits forms on supported ATS platforms via **Playwright**
- Writes everything to a **Google Sheet** for a clean application ledger

Every decision is logged. Every skipped job has a reason. Nothing is invented.

---

## Tech Stack

| Layer | Tools |
|---|---|
| Language | Python 3.12+ |
| Browser automation | Playwright (Chromium) |
| LLM scoring & Q&A | Anthropic Claude (Haiku for fast scoring, configurable) |
| Job discovery | ATS APIs, Apify scrapers, HTTP adapters |
| Persistence | SQLite ledger + Google Sheets |
| Config | YAML + `.env` |

---

## Pipeline Stages

### 1. Discover
Fetch fresh jobs from configured sources. ATS-direct sources (Greenhouse, Lever, Ashby) are preferred — cleaner metadata, real timestamps, canonical application URLs.

### 2. Normalize
All incoming jobs are converted into a unified `Job` schema (`src/models.py`). Fields include: company, title, location, remote policy, seniority, posting timestamp, ATS provider, tech stack, visa/relocation signals.

### 3. Gate
Fast rule-based pre-filter before any LLM call. Configurable per run:
- Max age (e.g. 48 hours)
- Max experience requirement (e.g. 0–1 years)
- Language requirements
- Target countries / remote policies

### 4. Dedupe
Canonical ATS URLs are resolved and checked against the SQLite ledger. Already-applied or already-seen jobs are skipped.

### 5. Score
Each gated job is scored against your `CANDIDATE.md` profile by an LLM. The rubric evaluates role fit, tech fit, seniority fit, location/relocation fit, and application feasibility. Configurable threshold (default `≥ 6.5 / 10`).

### 6. Review (human in the loop)
The pipeline **stops** after scoring and writes a review file to `manual_queue/`. You approve specific jobs before anything is submitted. Auto-apply without review confirmation never happens.

### 7. Apply
Playwright fills and submits forms on supported ATS platforms:

| ATS | Status |
|---|---|
| Greenhouse | Supported |
| Ashby | Supported |
| Lever | Supported |
| Workable | Supported |
| LinkedIn Easy Apply | Manual queue only |
| Workday | Manual queue only |
| CAPTCHA-protected | Manual queue only |

If the filler hits a field it cannot answer from your profile, it raises `NeedsUserInput` and routes the job to the manual queue — never guesses, never fabricates.

### 8. Sync & Report
Applied and queued jobs are written to Google Sheets. A session report is appended to `history/`.

---

## Quickstart

### Prerequisites

- Python 3.12+
- A Playwright-compatible system (macOS, Linux, Windows WSL2)
- An [Anthropic API key](https://console.anthropic.com/)
- A Google Cloud service account with Sheets API access (optional, for sheet sync)

### Setup

```bash
git clone https://github.com/your-username/jobpilot-autopilot-for-job-applications.git
cd jobpilot-autopilot-for-job-applications

python -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
playwright install chromium
```

### Configure

Copy the example files and fill in your details:

```bash
cp CANDIDATE.EXAMPLE.md CANDIDATE.md
cp .env.example .env
```

`CANDIDATE.md` is your single source of truth. The pipeline answers forms **only** from this file — no data is invented. See [`CANDIDATE.EXAMPLE.md`](CANDIDATE.EXAMPLE.md) for the full schema.

Your `.env` needs at minimum:

```env
ANTHROPIC_API_KEY=sk-ant-...
```

Adjust `config/config.yaml` for your target markets, score threshold, and apply settings.

### Run

```bash
# Discover and score — stops before applying
python main.py

# After reviewing manual_queue/<run_id>_review.md, apply approved jobs
python main.py --apply
```

---

## Project Structure

```
src/
  models.py          Job schema, Status enum, RemoteType
  normalize.py       Canonical job key builder, field normalizer
  gate.py            Pre-LLM filter rules
  ledger.py          SQLite dedup store
  score.py           LLM scoring rubric
  pipeline.py        Orchestration — wires all stages together
  sheet.py           Google Sheets sync
  apply/
    base.py          BaseFormFiller — shared helpers, submit(), prefetch()
    runner.py        Playwright session manager, filler dispatcher
    candidate.py     Loads CANDIDATE.md, resolves cover letter text
    forms/
      greenhouse_form.py
      ashby_form.py
      lever_form.py
      workable_form.py
  discover/
    arbeitnow.py     Example ATS-direct adapter
    ...              Add your own adapters here

config/
  config.yaml        Run configuration

assets/
  resume/            Your resume (gitignored)
  cover_letters/     Cover letter templates (gitignored)
```

---

## Adding a New Job Source

Create a module in `src/discover/` that yields `Job` objects:

```python
# src/discover/my_source.py
from src.models import Job, RemoteType, Route, Status
from src.normalize import build_job_key

def fetch(cfg: dict) -> list[Job]:
    jobs = []
    # ... your HTTP/scraping logic
    jobs.append(Job(
        job_key   = build_job_key(company, title, country),
        title     = title,
        company   = company,
        apply_url = url,
        ats       = "greenhouse",   # or "ashby", "lever", etc.
        source    = "my_source",
        ...
    ))
    return jobs
```

Then register it in `src/pipeline.py` alongside the existing adapters.

---

## Adding a New ATS Form Filler

Subclass `BaseFormFiller` in `src/apply/forms/`:

```python
# src/apply/forms/myats_form.py
from src.apply.base import BaseFormFiller, NeedsUserInput

class MyAtsForm(BaseFormFiller):
    def prefetch(self) -> None:
        # Run LLM calls here — before page.goto() — to avoid blocking the browser
        self._cl_text = self.candidate.cover_letter_text(
            self.job.title, self.job.company, description=self.job.description
        )

    def fill_form(self) -> None:
        p = self.page
        c = self.candidate
        # fill fields, handle custom questions, call self.submit(selector)
```

Register it in `src/apply/runner.py`:

```python
_FILLERS: dict[str, type[BaseFormFiller]] = {
    ...
    "myats": MyAtsForm,
}
```

The `prefetch()` hook exists because LLM API calls inside an active Playwright session can cause the browser context to close. Always do your async/blocking work in `prefetch()`, not `fill_form()`.

---

## Testing

Run the form filler smoke tests with a real browser:

```bash
# Test Ashby filler (dry_run=true by default — never submits)
python test_apply.py ashby

# Test Greenhouse filler
python test_apply.py greenhouse
```

Set `dry_run: false` in `config/config.yaml` only when you intend to actually submit an application.

---

## Configuration Reference

```yaml
# config/config.yaml

gate:
  max_age_hours: 48       # only jobs posted within this window
  max_yoe: 1              # skip roles requiring more years of experience
  languages: [EN]         # accepted posting languages

score:
  threshold: 6.5          # minimum LLM score to pass to apply stage
  model: claude-haiku-4-5-20251001  # swap to sonnet for higher quality

apply:
  dry_run: true           # true = fill form + screenshot, never click submit
  headless: true          # false = watch the browser fill the form
  max_attempts: 2
  resume_file: assets/resume/resume.pdf
  cover_letter_short: assets/cover_letters/cover_letter_short.md
  cover_letter_long: assets/cover_letters/cover_letter_long.md
```

---

## Safety & Ethics

- **Never fabricates answers.** Every field must map to a key in `CANDIDATE.md`. Unknown required fields route to manual review.
- **Human review is mandatory.** The pipeline never auto-applies without explicit confirmation.
- **PII stays local.** `CANDIDATE.md`, resumes, cover letters, secrets, and all generated artifacts are gitignored.
- **Respect platform ToS.** Do not bypass CAPTCHAs, rate limits, or login walls. The manual queue exists precisely for those flows.

---

## Contributing

Contributions are welcome. JobPilot is intentionally modular — the most impactful areas are:

- **New job source adapters** (`src/discover/`) — more boards, more niche markets
- **New ATS form fillers** (`src/apply/forms/`) — WorkDay, SmartRecruiters, Rippling, etc.
- **Scoring improvements** — better rubric prompts, prescore heuristics
- **Sheet / export integrations** — Notion, Airtable, CSV export
- **Testing** — more smoke tests, mock ATS fixtures

To contribute:

1. Fork the repo and create a feature branch
2. Keep changes focused — one adapter or one fixer per PR
3. Add or update the relevant smoke test
4. Open a PR with a short description of what problem it solves

If you are unsure whether something fits the project scope, open an issue first.

---

## License

MIT — use freely, build on it, ship your own version. Attribution appreciated but not required.
