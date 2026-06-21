# SPEC.md — jobpilot v2 build specification

> **For Claude Code.** Read `CLAUDE.md` first (guardrails, tier model, conventions),
> then build the project exactly as specified here. Files in §2–§4 are **verbatim**:
> create them byte-for-byte, do not "improve" them. Modules in §5–§7 are **contracts**:
> implement to the given signatures and behaviors. §8 is the definition of done.
> This file is the single source for the scaffold — do not invent structure beyond it.

## 1. Target tree

✓ = committed · ✗ = gitignored (created locally by `make setup`)

```
jobpilot-autopilot-for-job-applications/
├── .gitignore                  ✓
├── .env.example                ✓        .env                         ✗
├── .pre-commit-config.yaml     ✓        .piipatterns                 ✗
├── .claude/settings.json       ✓
├── CLAUDE.md  SPEC.md  SETUP.md  README.md  CANDIDATE.example.md   ✓
├── CANDIDATE.md                ✗
├── Makefile  requirements.txt  ✓
├── scripts/pii_guard.sh        ✓
├── config/{config,sources,scoring}.yaml  ✓
├── secrets/                    ✗   (google-sa.json)
├── assets/
│   ├── README.md               ✓   ("resume + letters go here; gitignored")
│   ├── cover_letters/{backend_fintech,ai_fullstack}.example.md  ✓
│   ├── cover_letters/*.md      ✗        resume/                  ✗
├── src/
│   ├── __init__.py  config.py  models.py  normalize.py  gate.py
│   ├── resolve.py  ledger.py  dedupe.py  pipeline.py  sheet.py  report.py
│   ├── score/{__init__,prescore,llm_score}.py
│   ├── apply/{__init__,router,answers,evidence}.py
│   ├── apply/forms/{__init__,base,greenhouse_form,lever_form,ashby_form,workable_form}.py
│   └── discover/
│       ├── __init__.py
│       ├── ats/{__init__,greenhouse}.py        # lever/ashby/workable: TODO adapters
│       ├── apis/{__init__,arbeitnow}.py
│       ├── scrapers/{__init__,apify_runner,linkedin}.py
│       └── boards/{__init__,wwr}.py
├── tests/{test_gate.py, fixtures/}     ✓
├── runs/  history/  manual_queue/  data/        ✗
```

## 2. Infra files — verbatim

### `.gitignore`

```gitignore
# ── PII (public repo — never commit) ──────────────
CANDIDATE.md
assets/resume/
assets/cover_letters/*
!assets/cover_letters/*.example.md

# ── secrets & credentials ─────────────────────────
.env
.env.*
!.env.example
secrets/
*service*account*.json
*credentials*.json
client_secret*.json
token*.json

# ── runtime state & outputs ───────────────────────
runs/
history/
manual_queue/
data/
*.sqlite
*.sqlite3
*.db

# ── browser automation ────────────────────────────
.auth/
storage_state.json
playwright-report/
test-results/

# ── apify local storage ───────────────────────────
apify_storage/
storage/

# ── python ────────────────────────────────────────
__pycache__/
*.py[cod]
.venv/
venv/
.pytest_cache/
.mypy_cache/
.ruff_cache/
*.egg-info/

# ── node / os / ide ───────────────────────────────
node_modules/
.DS_Store
.idea/
.vscode/
*.log
.piipatterns
```

### `.env.example`

```bash
ANTHROPIC_API_KEY=
APIFY_TOKEN=
ADZUNA_APP_ID=
ADZUNA_APP_KEY=
SHEET_ID=
GOOGLE_SA_JSON_PATH=./secrets/google-sa.json
```

### `.pre-commit-config.yaml`

```yaml
repos:
  - repo: https://github.com/gitleaks/gitleaks
    rev: v8.24.0
    hooks:
      - id: gitleaks
  - repo: local
    hooks:
      - id: pii-guard
        name: block personal identifiers in staged diff
        entry: bash scripts/pii_guard.sh
        language: system
        pass_filenames: false
```

### `scripts/pii_guard.sh`

```bash
#!/usr/bin/env bash
# Blocks commits whose staged diff contains personal identifiers.
# Patterns live in .piipatterns (gitignored) — one regex per line — so the
# public repo never reveals what it is guarding. Falls back to no-op if absent.
set -euo pipefail
[ -f .piipatterns ] || exit 0
if git diff --cached -U0 | grep -qiEf .piipatterns; then
  echo "pii-guard: personal identifier found in staged diff. Commit blocked." >&2
  exit 1
fi
```

### `requirements.txt`

```text
httpx>=0.27
pydantic>=2.7
PyYAML>=6.0
python-dotenv>=1.0
anthropic>=0.40
gspread>=6.0
google-auth>=2.30
playwright>=1.45
apify-client>=1.7
tenacity>=8.3
pytest>=8.0
```

### `Makefile`

```makefile
.PHONY: setup discover gate score plan apply sync report run sweep test

PY := .venv/bin/python
PIP := .venv/bin/pip

setup:
	python3 -m venv .venv
	$(PIP) install -q -r requirements.txt
	$(PY) -m playwright install chromium
	$(PY) -m pip install -q pre-commit && .venv/bin/pre-commit install
	mkdir -p runs history manual_queue secrets data assets/resume assets/cover_letters
	test -f .env || cp .env.example .env
	test -f CANDIDATE.md || cp CANDIDATE.example.md CANDIDATE.md
	test -f assets/cover_letters/backend_fintech.md || cp assets/cover_letters/backend_fintech.example.md assets/cover_letters/backend_fintech.md
	test -f assets/cover_letters/ai_fullstack.md || cp assets/cover_letters/ai_fullstack.example.md assets/cover_letters/ai_fullstack.md
	@echo "--> Now fill .env, CANDIDATE.md, drop your resume into assets/resume/, edit cover letters."

discover:
	$(PY) -m src.pipeline discover

gate:
	$(PY) -m src.pipeline gate

score:
	$(PY) -m src.pipeline score

plan:
	$(PY) -m src.pipeline plan

apply:
	$(PY) -m src.pipeline apply

sync:
	$(PY) -m src.pipeline sync

report:
	$(PY) -m src.pipeline report

run:
	$(PY) -m src.pipeline run

sweep:
	$(PY) -m src.pipeline sweep

test:
	$(PY) -m pytest -q
```

### `.claude/settings.json`

```json
{
  "permissions": {
    "allow": [
      "Bash(make:*)",
      "Bash(.venv/bin/python -m src.pipeline:*)",
      "Bash(.venv/bin/pytest:*)",
      "Bash(git status)",
      "Bash(git diff:*)",
      "Bash(git log:*)"
    ],
    "deny": ["Read(./.env)", "Read(./secrets/**)", "Read(./.piipatterns)"]
  }
}
```

## 3. Configs — verbatim

### `config/config.yaml`

```yaml
mode: full_auto # review_first | full_auto

markets:
  countries: [DE, NL, IE, GB, TR]
  remote_worldwide: true

limits:
  per_country_min: 15 # discovery target, not a guarantee
  turkey_max: 50

gate:
  max_age_hours: 48
  max_yoe: 1
  languages: [EN, TR] # DE-language postings pass only if german_ok
  german_ok: true # candidate has B2 — accept German postings

score:
  threshold: 6.5
  model: claude-haiku-4-5-20251001
  max_llm_calls_per_run: 120 # cost guard; prescore must cut volume below this

apply:
  dry_run: false # true = fill + screenshot, never click submit
  headless: true
  max_attempts: 2
  resume_file: assets/resume/resume.pdf

paths:
  runs: runs
  history: history
  manual_queue: manual_queue
  ledger: data/ledger.sqlite

sheet:
  worksheet: "ALL APPLICATIONS"
  columns:
    [
      COUNTRY,
      COMPANY,
      POSITION,
      LOCATION,
      TYPE,
      SALARY,
      LANGUAGE,
      STAGE,
      SITUATION,
    ]
  stage_applied: APPLIED
  stage_queued: QUEUED
  situation_default: "ON GOING"
  country_display: # ISO2 -> sheet display value
    DE: Germany
    NL: Netherlands
    IE: Ireland
    GB: UK / England
    TR: Turkey
    US: USA
    REMOTE: Worldwide

ghost_after_days: 21
```

### `config/sources.yaml`

```yaml
# Adapter registry. name -> module under src/discover/. Disabled adapters are
# skipped by `make discover`. Tier numbers map to the trust model in CLAUDE.md.

ats: # tier 1 — trusted timestamps
  greenhouse:
    enabled: true
    # Board slug = boards.greenhouse.io/<slug>. Expand this seed list freely;
    # slugs are public. Keep it biased to DE/NL/IE/UK + remote-friendly cos.
    boards: [gitlab, datadog, cloudflare, personio, mollie, adyen, intercom]
  lever:
    enabled: false # TODO(adapter): src/discover/ats/lever.py
    companies: [] # jobs.lever.co/<company>
  ashby:
    enabled: false # TODO(adapter): src/discover/ats/ashby.py
    orgs: [] # jobs.ashbyhq.com/<org>
  workable:
    enabled: false # TODO(adapter)
    accounts: []

apis: # tier 2 — trusted timestamps
  arbeitnow:
    enabled: true
    visa_sponsorship_only: true
  adzuna:
    enabled: false # set ADZUNA_APP_ID/KEY in .env first
    countries: [de, nl, gb] # check IE availability before adding

scrapers: # tier 3 — discovery only, must resolve
  linkedin:
    enabled: false # needs APIFY_TOKEN; keep off until forms work
    actor: curious_coder/linkedin-jobs-scraper
    # f_TPR=r172800 -> last 48h, f_E=1,2,3 -> internship/entry/associate
    search_urls:
      - "https://www.linkedin.com/jobs/search/?keywords=software%20engineer&location=Germany&f_TPR=r172800&f_E=1%2C2%2C3"
      - "https://www.linkedin.com/jobs/search/?keywords=software%20engineer&location=Netherlands&f_TPR=r172800&f_E=1%2C2%2C3"
      - "https://www.linkedin.com/jobs/search/?keywords=software%20engineer&location=Ireland&f_TPR=r172800&f_E=1%2C2%2C3"
      - "https://www.linkedin.com/jobs/search/?keywords=software%20engineer&location=United%20Kingdom&f_TPR=r172800&f_E=1%2C2%2C3"
      - "https://www.linkedin.com/jobs/search/?keywords=ai%20engineer&location=European%20Union&f_TPR=r172800&f_E=1%2C2%2C3"

boards: # tier 4
  wwr: { enabled: false } # TODO: RSS — easiest board, do first
  landingjobs: { enabled: false }
  relocateme: { enabled: false }
  ukhired: { enabled: false }
  kariyer: { enabled: false }
  englishjobs: { enabled: false }
```

### `config/scoring.yaml`

```yaml
threshold: 6.5

weights:
  role: 0.25 # does the day-to-day match full-stack/backend/AI-engineer work
  stack: 0.25 # overlap with candidate stack
  seniority: 0.20 # true new-grad/junior fit (<=1 YoE expected)
  visa: 0.20 # sponsorship/relocation/remote-from-anywhere signal
  culture: 0.10 # product company, engineering-led, English working language

prescore:
  min_overlap: 0.15 # share of stack_keywords hit in title+description to survive
  stack_keywords:
    - node
    - node.js
    - typescript
    - javascript
    - react
    - react native
    - c#
    - .net
    - python
    - java
    - mongodb
    - sql
    - aws
    - docker
    - rest
    - api
    - microservices
    - oauth
    - llm
    - openai
    - gpt
    - rag
    - machine learning
    - ai

rubric: |
  Score each dimension 0-10. Be strict: 8+ is rare.
  - role: 9-10 = junior full-stack/backend/AI-engineer building product features;
    5-7 = adjacent (data, platform, QA-leaning); 0-4 = mismatched discipline.
  - stack: proportion of the posting's required stack the candidate already
    ships with in production. Nice-to-haves count half.
  - seniority: 9-10 = explicit new-grad/junior/0-1y; 5-7 = unstated but scope
    reads junior; 0-4 = any hint of 2+ years required or ownership beyond junior.
  - visa: 9-10 = explicit sponsorship/relocation or remote-from-anywhere;
    5-7 = silent but company is a known sponsor; 0-4 = local-only phrasing.
  - culture: English-speaking, product/engineering-led, structured onboarding.
```

## 4. Foundation — verbatim (everything downstream depends on these interfaces)

### `src/models.py`

```python
"""Typed data contracts. Every stage reads/writes these models as JSONL."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from hashlib import sha256

from pydantic import BaseModel, Field


class RemoteType(str, Enum):
    remote = "remote"
    hybrid = "hybrid"
    onsite = "onsite"
    unknown = "unknown"


class Route(str, Enum):
    auto = "auto"
    manual = "manual"


class Status(str, Enum):
    new = "new"
    gated_out = "gated_out"
    scored = "scored"
    planned = "planned"
    applied = "applied"
    queued = "queued"
    skipped = "skipped"
    failed = "failed"


class Score(BaseModel):
    role: float
    stack: float
    seniority: float
    visa: float
    culture: float
    total: float
    rationale: str = ""


class Job(BaseModel):
    job_key: str
    title: str
    company: str
    country: str = "REMOTE"               # ISO2 or REMOTE
    location: str | None = None
    remote: RemoteType = RemoteType.unknown

    posted_at: datetime | None = None
    timestamp_trusted: bool = False        # only tier 1/2 adapters set True

    source: str
    source_tier: int
    ats: str | None = None
    apply_url: str
    salary: str | None = None
    language: str = "EN"
    description: str = ""

    yoe_max: int | None = None
    visa_signal: bool = False

    score: Score | None = None
    route: Route | None = None
    cover_letter: str | None = None        # path chosen at plan stage
    status: Status = Status.new
    gate_reason: str | None = None
    fail_reason: str | None = None

    def url_hash(self) -> str:
        return sha256(self.apply_url.encode()).hexdigest()[:16]

    def age_hours(self, now: datetime | None = None) -> float | None:
        if self.posted_at is None:
            return None
        now = now or datetime.now(timezone.utc)
        posted = self.posted_at
        if posted.tzinfo is None:
            posted = posted.replace(tzinfo=timezone.utc)
        return (now - posted).total_seconds() / 3600


class ApplicationRecord(BaseModel):
    job_key: str
    url_hash: str
    company: str
    title: str
    country: str
    status: Status
    run_id: str
    applied_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    cover_letter: str | None = None
    evidence_dir: str | None = None
    reason: str | None = None
```

### `src/gate.py`

```python
"""Freshness / seniority / language gate. Ported trust model from v1:
real timestamps only — untrusted or missing posted_at is an automatic reject."""
from __future__ import annotations

import re
from typing import Any

from .models import Job

SENIOR_RE = re.compile(
    r"\b(senior|sr\.?|staff|principal|lead|head of|director|manager|architect"
    r"|expert|iii|iv)\b",
    re.I,
)
JUNIOR_RE = re.compile(
    r"\b(junior|jr\.?|graduate|new ?grad|entry[- ]?level|intern(ship)?"
    r"|working student|werkstudent|early careers?|trainee|associate|campus)\b",
    re.I,
)
YOE_RE = re.compile(r"(\d+)\s*\+?\s*(?:years?|yrs?|jahren?|jaar)", re.I)
GERMAN_HINT_RE = re.compile(r"[äöüß]|deutschkenntnisse|fließend deutsch", re.I)


def freshness_ok(job: Job, max_age_hours: int) -> tuple[bool, str]:
    if job.posted_at is None or not job.timestamp_trusted:
        return False, "no_trusted_timestamp"
    age = job.age_hours()
    if age is None or age > max_age_hours:
        return False, f"stale_{int(age or -1)}h"
    return True, ""


def seniority_ok(job: Job, max_yoe: int) -> tuple[bool, str]:
    if SENIOR_RE.search(job.title) and not JUNIOR_RE.search(job.title):
        return False, "senior_title"
    text = f"{job.title} {job.description}"
    mentions = [int(m) for m in YOE_RE.findall(text)]
    floor = min(mentions) if mentions else None
    if floor is not None and floor > max_yoe and not JUNIOR_RE.search(job.title):
        return False, f"requires_{floor}yoe"
    if job.yoe_max is not None and job.yoe_max > max_yoe:
        return False, f"requires_{job.yoe_max}yoe"
    return True, ""


def language_ok(job: Job, cfg: dict[str, Any]) -> tuple[bool, str]:
    if job.language.upper() in {l.upper() for l in cfg["languages"]}:
        return True, ""
    if job.language.upper() == "DE" and cfg.get("german_ok"):
        return True, ""
    # Heuristic: postings without an explicit language tag but German-only text
    if GERMAN_HINT_RE.search(job.description[:2000]) and not cfg.get("german_ok"):
        return False, "german_required"
    return (True, "") if job.language == "EN" else (False, f"lang_{job.language}")


def gate(job: Job, cfg: dict[str, Any]) -> tuple[bool, str]:
    """Returns (passed, reason). Reason is empty on pass."""
    for check, arg in (
        (freshness_ok, cfg["max_age_hours"]),
        (seniority_ok, cfg["max_yoe"]),
        (language_ok, cfg),
    ):
        ok, reason = check(job, arg)  # type: ignore[arg-type]
        if not ok:
            return False, reason
    return True, ""
```

### `tests/test_gate.py`

```python
"""Gate behavior — the contract tier-3 sources are held to."""
from datetime import datetime, timedelta, timezone

from src.gate import gate
from src.models import Job

CFG = {"max_age_hours": 48, "max_yoe": 1, "languages": ["EN", "TR"],
       "german_ok": True}


def _job(**kw) -> Job:
    base = dict(
        job_key="acme__junior-engineer__de", title="Junior Software Engineer",
        company="acme", country="DE", source="test", source_tier=1,
        apply_url="https://boards.greenhouse.io/acme/jobs/1",
        posted_at=datetime.now(timezone.utc) - timedelta(hours=5),
        timestamp_trusted=True,
    )
    base.update(kw)
    return Job(**base)


def test_fresh_junior_passes():
    ok, reason = gate(_job(), CFG)
    assert ok, reason


def test_untrusted_timestamp_rejected():
    ok, reason = gate(_job(timestamp_trusted=False), CFG)
    assert not ok and reason == "no_trusted_timestamp"


def test_stale_rejected():
    ok, reason = gate(
        _job(posted_at=datetime.now(timezone.utc) - timedelta(hours=72)), CFG)
    assert not ok and reason.startswith("stale")


def test_senior_title_rejected():
    ok, reason = gate(_job(title="Senior Backend Engineer"), CFG)
    assert not ok and reason == "senior_title"


def test_yoe_requirement_rejected():
    ok, reason = gate(
        _job(title="Software Engineer",
             description="We require 5+ years of experience with Java."), CFG)
    assert not ok and reason == "requires_5yoe"


def test_junior_title_with_yoe_conflict_defers_to_llm():
    # Conflicting signals (junior title, 5+ years in body) pass the cheap gate
    # on purpose — the LLM seniority dimension handles ambiguity (see rubric).
    ok, _ = gate(
        _job(description="Our team has 5+ years of experience."), CFG)
    assert ok


def test_junior_override_beats_senior_regex():
    ok, _ = gate(_job(title="Junior Engineer (Graduate Lead Program)"), CFG)
    assert ok
```

## 5. Module contracts — implement to these signatures

Conventions for all modules: Python 3.11+, full type hints, pydantic v2 models at
stage boundaries, `httpx` for IO, `logging` not `print`, no global state outside
the ledger, every stage idempotent against its `runs/<RUN_ID>/` artifacts.

### `src/config.py`

- `ROOT = Path(__file__).resolve().parent.parent`
- `load(name: str) -> dict` — reads `config/<name>.yaml`; caches per-process.
- `env(key: str, required: bool = False) -> str | None` — `.env` via python-dotenv;
  raises `RuntimeError` naming the key when `required` and missing.

### `src/normalize.py`

- `slug(text) -> str` — lowercase, non-alphanumeric → `-`, trimmed.
- `build_job_key(company, title, country) -> str` — `f"{slug(company)}__{slug(title)}__{slug(country)}"`.
  **This exact format is shared with `sheet.existing_keys()` — never change one side alone.**
- `country_from_location(location) -> str` — hint map (city/country names incl.
  Berlin/München/Amsterdam/Dublin/London/Istanbul…) → ISO2 (`GB` for UK); no match → `"REMOTE"`.
- `infer_remote(*texts) -> RemoteType` — regex `remote|work from home|wfh|anywhere`
  → remote; `hybrid` → hybrid; else unknown.

### `src/resolve.py`

- `fingerprint(url) -> str | None` — host/path regexes → `"greenhouse" | "lever" |
"ashby" | "workable" | "smartrecruiters" | "recruitee" | "personio" | "workday" |
"taleo" | "icims" | "successfactors" | "linkedin"` or None.
- `resolve(url, timeout=15.0) -> tuple[str, str | None]` — follows redirects
  (httpx, tenacity 2 retries, exp backoff) to the final URL; returns
  `(final_url, fingerprint(final_url))`. Network failure → return input URL with None.

### `src/ledger.py`

- SQLite at `paths.ledger`; table `applications(job_key TEXT, url_hash TEXT,
company, title, country, status, run_id, applied_at, cover_letter,
evidence_dir, reason, PRIMARY KEY (job_key, url_hash))`.
- `Ledger(path)` creates parent dirs + schema. `seen(job_key, url_hash) -> bool`
  matches on EITHER key (same job re-discovered under a different URL still dedupes).
- `record(rec: ApplicationRecord)` — upsert.

### `src/dedupe.py`

- `filter_new(jobs, ledger, sheet_keys) -> list[Job]` — drop if
  `ledger.seen(...)` or `job.job_key in sheet_keys`; log dropped count.

### `src/score/prescore.py`

- `overlap(job, keywords) -> float` — fraction of `scoring.prescore.stack_keywords`
  found (substring, case-insensitive) in `title + description`.
- `survives(job, cfg) -> bool` — `overlap >= prescore.min_overlap`.

### `src/score/llm_score.py`

- `score_job(job, candidate, scoring_cfg) -> Score` — Anthropic client, model from
  `config.score.model`. System prompt: strict JSON only. User content: rubric text +
  weights + candidate **PII-free profile summary** (never name/phone/email) +
  title/company/country/description (desc truncated ~6k chars).
- Parse JSON (strip code fences defensively); `total = Σ dim*weight` recomputed
  locally — never trust the model's arithmetic. Malformed response after 1 retry
  → `Score(all dims 0, rationale="llm_parse_error")` so the job fails threshold visibly.

### `src/apply/router.py`

- `AUTO_ATS = {"greenhouse","lever","ashby","workable"}`,
  `HARD_MANUAL = {"workday","taleo","icims","successfactors","linkedin"}`.
- `route(job) -> Route` — fingerprint of `apply_url`: in AUTO_ATS → auto;
  anything else (incl. None/unknown) → manual. Guardrail #3 lives here.

### `src/apply/answers.py`

- `class UnresolvableAnswer(Exception)` carrying the exact question text.
- `Candidate.load(path="CANDIDATE.md")` — parse `## section` blocks and
  `- Key: value` pairs; expose `identity`, `facts` (dict), `facts_bank` (list),
  `profile_summary` (str).
- `resolve_fact(question) -> str` — normalize question; token-subset match against
  known keys + synonym map (e.g. "notice period", "salary expectation",
  "require sponsorship", "years of experience"); no confident match → raise
  `UnresolvableAnswer(question)`. **Never returns a guess** (guardrail #1).
- `compose(question, job_description) -> str` — TODO #6 stub: raise
  `UnresolvableAnswer` (docstring: LLM-composed from facts bank only).

### `src/apply/evidence.py`

- `evidence_dir(run_dir, job_key) -> Path` (mkdir -p `runs/<id>/evidence/<job_key>/`),
  `save_payload(d, payload)` → `payload.json`, `screenshot(page, d, name)` → png.

### `src/apply/forms/base.py`

- `CaptchaDetected(Exception)`; `_CAPTCHA_RE = captcha|hcaptcha|recaptcha|cf-turnstile`.
- `ApplyResult` dataclass: `ok: bool, submitted: bool, reason: str | None`.
- `BaseForm(headless, dry_run, resume_path)` with abstract `fill(page, job,
candidate) -> payload dict`, `submit(page)`, `confirm(page) -> bool`.
- `apply(job, candidate, ev_dir) -> ApplyResult` lifecycle: launch chromium →
  goto apply_url → captcha check → `fill` → screenshot `01_filled` + payload →
  if `dry_run`: return ok/not-submitted → `submit` → `confirm` →
  screenshot `02_confirmation` → ok. `UnresolvableAnswer` / `CaptchaDetected` /
  validation errors → ApplyResult(ok=False, reason=...); browser always closed.

### `src/discover/__init__.py`

- `_TIERS = {"ats":1,"apis":2,"scrapers":3,"boards":4}`.
- `run_enabled(sources_cfg, markets) -> list[Job]` — for each enabled adapter,
  importlib `src.discover.<pkg>.<name>`, call `fetch(params, markets)`, set
  `source`/`source_tier`; adapter exception → log + continue (one source never
  kills discovery).

### `src/discover/ats/greenhouse.py` (tier-1 reference adapter)

- `API = "https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true"`.
- For each board slug: jobs → Job with `posted_at = first_published or updated_at`,
  `timestamp_trusted=True`, `country = country_from_location(location.name)`,
  `apply_url = absolute_url`, `ats="greenhouse"`, html-stripped `description`.

### `src/discover/apis/arbeitnow.py` (tier-2)

- `API = "https://www.arbeitnow.com/api/job-board-api"`, pages 1–3.
- `visa_sponsorship_only` honors the visa flag; `posted_at` from unix `created_at`
  (`timestamp_trusted=True`); `visa_signal=True` when flagged; `language` "DE" if
  the description trips the German-hint regex else "EN".

### `src/discover/scrapers/apify_runner.py`

- `run_actor(actor, run_input, token_env="APIFY_TOKEN") -> list[dict]` —
  apify-client, sync run, returns dataset items; missing token → RuntimeError.

### `src/sheet.py`

- gspread + service account from `GOOGLE_SA_JSON_PATH`; sheet from `SHEET_ID` env.
- `existing_keys(cfg) -> set[str]` — read all rows of worksheet `"ALL APPLICATIONS"`;
  rebuild keys via `build_job_key(COMPANY, POSITION, iso2(COUNTRY))` using the
  inverted `country_display` map — manual sheet rows must block re-apply.
- `append_jobs(jobs, cfg) -> int` — map Job → row in the exact column order
  `[COUNTRY, COMPANY, POSITION, LOCATION, TYPE, SALARY, LANGUAGE, STAGE, SITUATION]`;
  COUNTRY via `country_display`; TYPE from `remote`; STAGE `APPLIED`/`QUEUED`;
  SITUATION `"ON GOING"`; batch append.
- `sweep(cfg, ghost_after_days) -> int` — TODO #8 stub: `NotImplementedError`
  (needs the applied-date column mapping — ask the user first).

### `src/report.py`

- `build(run_id, stages, history_dir) -> Path` — markdown: funnel table
  (discovered → gated → scored → planned → applied/queued), per-country and
  per-source tables, top gate-reject reasons, failures with reasons, manual-queue
  checkbox list. Writes `history/<date>_<run>_session.md`, prepends a line to
  `history/index.md`.

## 6. Stubs — create with contract docstring + `NotImplementedError`

`src/apply/forms/greenhouse_form.py` (TODO #1) must document its selector contract:
`#first_name #last_name #email #phone`, resume `input[type=file]`, label-driven
custom questions → `candidate.resolve_fact(label)`, submit `button#submit_app`,
confirmation = `Thank you` text match. Same pattern files for
`lever_form / ashby_form / workable_form` (TODO #2–4), `discover/scrapers/linkedin.py`
(TODO #5 — discovery-only, every item must go through `src.resolve`; relative dates
never trusted), `discover/boards/wwr.py` (TODO #7 — RSS).

Also create `assets/README.md` (one line: resume + cover letters live here,
gitignored) and the two cover-letter templates
`assets/cover_letters/{backend_fintech,ai_fullstack}.example.md` — short
placeholder letters using `<COMPANY>` / `<ROLE>` tokens, zero personal data.

## 7. `src/pipeline.py` — orchestrator (argparse: stage as positional arg)

- Run dirs: `runs/<UTC yyyymmdd-HHMMSS>/`; `RUN_ID` env targets an existing run,
  default = newest. Stage artifacts: `raw.jsonl → gated.jsonl → scored.jsonl →
planned.jsonl → applied.jsonl` (one Job JSON per line).
- `discover` — `run_enabled` → raw.jsonl (+ per-source counts logged).
- `gate` — tier ≥ 3 items first pass through `resolve()` (canonical URL + ats
  fingerprint; unresolved keep None and will fail freshness); then `gate()`;
  rejects logged with reasons into the run dir.
- `score` — `dedupe.filter_new` (ledger + `sheet.existing_keys`, sheet errors
  non-fatal) → prescore → LLM (stop at `max_llm_calls_per_run`) → keep
  `total >= threshold`, sort by `(age_hours asc, total desc)`.
- `plan` — `route()`; cover letter heuristic: AI terms (`ai|llm|ml|machine
learning|rag`) in title+description → `ai_fullstack.md` else
  `backend_fintech.md`; write planned.jsonl + `manual_queue/<run>.md` review
  cards: `- [ ] **title** — company (country) · age · score/10` + rationale +
  direct apply_url + queue reason.
- `apply` — guardrail #2: in `review_first`, refuse without `APPROVE=1` env.
  Iterate auto-routed: form class by `job.ats`; missing filler →
  queue `no_filler_<ats>`; ≤ `apply.max_attempts`; evidence dir per job;
  `ledger.record` everything; continue until every auto-routed job has been attempted.
- `sync` — `append_jobs(applied + queued)` minus keys already in the sheet.
- `report` — `report.build` over all stage artifacts.
- `run` — discover→gate→score→plan, then apply+sync only in `full_auto`;
  report always. One bad job never crashes a stage (collect, continue).

## 8. Definition of done (scaffold task)

1. Tree matches §1; verbatim files byte-identical to §2–§4.
2. `make setup` then `make test` → all 7 gate tests green, zero network in tests.
3. `.venv/bin/python -m src.pipeline discover` produces `runs/<id>/raw.jsonl`
   with greenhouse + arbeitnow items (network OK here).
4. `python -m src.pipeline apply` in `review_first` without `APPROVE=1` exits
   with a refusal message (guardrail #2 proof).
5. `git status` shows **no** PII paths (`CANDIDATE.md`, `.env`, `assets/resume/`,
   `secrets/`, `runs/`, `history/`, `manual_queue/`, `data/`).
6. `pre-commit run --all-files` passes.
