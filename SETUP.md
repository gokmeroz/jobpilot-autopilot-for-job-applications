# SETUP

## 0. Prerequisites

- Python 3.11+, git, a Google account, an Anthropic API key, (later) an Apify token
- VS Code ≥ 1.98

## 1. Seed the repo (docs only — no code yet)

```bash
git clone https://github.com/gokmeroz/jobpilot-autopilot-for-job-applications.git
cd jobpilot-autopilot-for-job-applications
# drop in: CLAUDE.md  SPEC.md  SETUP.md  CANDIDATE.example.md  README.md (overwrite)
git add -A && git commit -m "docs: playbook, build spec, candidate template" && git push
```

All five files are PII-free by design — safe in the public repo. Everything
personal arrives only in step 4, after `.gitignore` exists.

## 2. VS Code + Claude Code

1. Open the repo folder in VS Code.
2. Extensions view (`Cmd+Shift+X`) → search **"Claude Code"** (publisher:
   Anthropic) → Install. The extension bundles the CLI.
3. Open the panel via the Spark icon (or `Cmd+Esc`); sign in when prompted.
4. `CLAUDE.md` at the repo root loads automatically — guardrails, commands,
   build order. The integrated terminal has the full `claude` CLI if you
   prefer it.

## 3. Scaffold (Claude Code task #0)

Start in **Plan mode**, paste:

```
Read CLAUDE.md, then build the project exactly per SPEC.md. Verbatim sections
(§2–§4) byte-for-byte; contract modules (§5–§7) to their signatures. Finish by
satisfying SPEC §8: make setup, make test green, discover smoke test, and show
git status to prove no PII paths are staged.
```

Review the plan, approve, then:

```bash
git add -A
git status   # MUST NOT list: CANDIDATE.md, .env, assets/resume, secrets/, runs/, data/
git commit -m "scaffold: pipeline skeleton, configs, guardrails"
git push
```

After this commit, `.claude/settings.json` exists — it pre-allows `make`/pytest
and **denies reads of `.env`, `secrets/`, `.piipatterns`**. Tune with `/permissions`.

## 4. Fill the gitignored files (`make setup` created them)

1. **`.env`** — `ANTHROPIC_API_KEY`, `SHEET_ID` (from the sheet URL:
   `docs.google.com/spreadsheets/d/<SHEET_ID>/edit`). Apify/Adzuna keys can wait.
2. **`CANDIDATE.md`** — fill every section. The pipeline refuses to answer any
   form question it can't derive from this file.
3. **`assets/resume/resume.pdf`** — your ATS resume (filename must match
   `apply.resume_file` in `config/config.yaml`).
4. **`assets/cover_letters/*.md`** — edit the two copied templates.
5. **`.piipatterns`** — one regex per line (your email, phone, surname); the
   pre-commit hook blocks any staged diff matching them.

## 5. Google Sheets service account (one-time, ~5 min)

1. console.cloud.google.com → new project → enable **Google Sheets API**.
2. IAM & Admin → Service Accounts → create → Keys → add JSON key.
3. Save the JSON as `secrets/google-sa.json` (gitignored).
4. Open your tracker sheet → Share → add the service account's
   `...@...iam.gserviceaccount.com` email as **Editor**.

## 6. First run (review_first mode)

```bash
make run            # discover → gate → score → plan → report; stops before apply
# review runs/<id>/planned.jsonl and manual_queue/<id>.md
APPROVE=1 make apply
make sync report
```

Form fillers are TODO #1–4 in CLAUDE.md — until they're implemented, every
auto-routed job lands in the queue with `no_filler_*`, which is the expected,
safe behavior.

## 7. Build out the fillers (Claude Code, in order)

```
Implement TODO #1 (GreenhouseForm) per the contract in
src/apply/forms/greenhouse_form.py. Set apply.dry_run=true in config and
verify against one real Greenhouse posting. Add the mapping test.
```

```
TODO #2: lever adapter + form. Same definition of done.
```

```
make run produced runs/<id>. Read the report's failures section and fix the
top recurring failure.
```

## 8. Daily loop once fillers exist

```bash
make run                      # morning
# skim manual_queue/<id>.md, tick what you applied to by hand
APPROVE=1 make apply && make sync report
```

Flip `mode: full_auto` in `config/config.yaml` only after ~3 clean
review_first sessions (no wrong answers in evidence screenshots).
