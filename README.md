# jobpilot — autopilot for job applications

Open-source pipeline that discovers fresh (≤48h) early-career software/AI roles
across DE · NL · IE · UK · TR + worldwide-remote, scores them against your CV
with an LLM rubric, auto-applies on supported ATSs (Greenhouse, Lever, Ashby,
Workable), queues the rest for one-click manual review, syncs everything to a
Google Sheet, and writes a session report.

```
discover → gate → score → plan → apply → sync → report
              (auto: Playwright)  (manual queue: review cards)
```

## Quickstart

See [SETUP.md](SETUP.md). Short version:

```bash
make setup          # venv, deps, playwright, pre-commit, local dirs
# fill .env, CANDIDATE.md, assets/ — all gitignored
make run            # review_first mode: stops after plan
APPROVE=1 make apply && make sync report
```

## Design principles

- **Trust real timestamps only.** ATS-direct APIs are tier 1; scraped sources
  are discovery-only and must resolve to a canonical ATS link.
- **Never fabricate answers.** Every form answer derives from `CANDIDATE.md`
  or the job is queued for a human.
- **No ToS roulette.** LinkedIn Easy Apply, Workday, CAPTCHA flows are never
  automated — they route to a manual queue by design.
- **PII never enters git.** Personal data lives only in gitignored files;
  a pre-commit guard blocks accidents.

MIT-style use at your own risk. Respect the terms of the sites you query.