# CANDIDATE.md — single source of truth for application answers

> Copy this file to `CANDIDATE.md` (gitignored) and fill every section.
> The pipeline answers forms ONLY from `CANDIDATE.md`. Two answer classes:
>
> - **FACT** — must map to a key below (name, phone, notice period, visa…).
>   No key → the job goes to the manual queue. The pipeline never guesses.
> - **COMPOSED** — free-text fields ("Why us?"). May be generated, but only
>   from the Facts bank below + the job description. Never invented.

## Identity

- Full name: <FULL NAME>
- Email: <EMAIL>
- Phone: <+CC-XXX-XXX-XX-XX>
- Current location: <City, Country>
- GitHub: <https://github.com/...>
- LinkedIn: <https://linkedin.com/in/...>
- Portfolio: <https://...>

## Profile summary (PII-free — used verbatim in LLM scoring prompts)

<2–3 sentences: stack, shipped products, experience level. No name, no
contact details — this text leaves the machine in API calls.>

## Work authorization (per country)

| Country | Right to work | Sponsorship needed | Notes |
|---|---|---|---|
| TR | yes | no | citizen |
| DE | no | yes | open to Chancenkarte/Blue Card routes |
| NL | no | yes | HSM sponsor required |
| IE | no | yes | CSEP eligible role required |
| UK | no | yes | Skilled Worker sponsor required |
| US (remote) | no | yes | remote-from-TR acceptable |

- Willing to relocate: yes — <at own expense / employer-supported>
- Remote: yes, worldwide; overlap with EU timezones full, US East ≥4h

## Logistics

- Notice period: <e.g. 0 — immediately available>
- Earliest start date: <YYYY-MM-DD or "immediately">
- Salary expectation:

| Market | Currency | Range |
|---|---|---|
| DE | EUR | <e.g. 50,000–60,000> |
| NL | EUR | <…> |
| IE | EUR | <…> |
| UK | GBP | <…> |
| TR | TRY | <…> |
| Remote (US co.) | USD | <…> |

## Canonical answers (FACT lookups — exact strings submitted)

- Years of professional experience: <e.g. "1">
- Highest education: <e.g. "B.Sc. Computer Engineering, 2025">
- English level: <e.g. "Full professional proficiency">
- German level: <e.g. "B2">
- Turkish level: Native
- Requires visa sponsorship (EU/UK): Yes
- Authorized to work in the EU: No
- Willing to work hybrid/on-site in <city list>: Yes
- How did you hear about us: "Job board"
- Pronouns: <…/prefer not to say>

## Facts bank (only source for COMPOSED answers)

- <Shipped product fact — what, stack, users/scale>
- <Internship fact — company type, system, scale>
- <AI/LLM integration fact — what you built, measurable outcome>
- <Research/program fact>
- <Motivation fact — why EU / why this domain>

## EEO / demographics defaults

- Gender / ethnicity / orientation / religion: "Prefer not to say"
- Disability status (US forms): <"I do not wish to answer" unless you choose otherwise>
- Veteran status (US forms): "I am not a protected veteran"

## Hard rule

If a required form field is not derivable from this file, DO NOT answer it.
Route the job to the manual queue and log the exact question text.