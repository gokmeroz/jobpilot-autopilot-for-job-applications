from __future__ import annotations

import json
import logging

from openai import OpenAI

from src.config import ROOT, env, load
from src.models import Job, Score, Status

log = logging.getLogger(__name__)

MODEL = "gpt-5.5"

_SYSTEM = (
    "You are a precise job-fit scorer for a software engineering candidate.\n"
    "Score each dimension 0–10. Return ONLY a JSON object — no prose, no markdown.\n"
    "Never invent facts. Base every score strictly on the provided profile and job description."
)


def _candidate_profile() -> str:
    return (ROOT / "CANDIDATE.md").read_text()


def _build_prompt(job: Job) -> str:
    return f"""\
## Candidate Profile
{_candidate_profile()}

## Job to Score
Title: {job.title}
Company: {job.company}
Country: {job.country}
Remote type: {job.remote.value}
Visa/sponsorship signal in posting: {job.visa_signal}
Salary: {job.salary or "not stated"}

### Job Description
{job.description or "No description provided."}

## Scoring Instructions
Score each dimension from 0 to 10:

- role (weight 25%): How well the role title and responsibilities match the candidate's target roles.
- stack (weight 20%): Tech stack overlap with the candidate's strong stack.
- seniority (weight 20%): Is this genuinely entry-level/junior/new-grad? 10 = perfect, 0 = senior or 5+ YOE required.
- visa (weight 20%): Probability that relocation, sponsorship, or remote is viable given country and signals.
- culture (weight 10%): Company quality, product focus, startup energy, growth trajectory.
- feasibility (weight 5%): Can we actually apply? Score 10 if apply URL is present and no blockers.

Compute: total = (role*0.25) + (stack*0.20) + (seniority*0.20) + (visa*0.20) + (culture*0.10) + (feasibility*0.05)

Write a one-sentence rationale explaining the total score.

Return ONLY this JSON:
{{
  "role": <float>,
  "stack": <float>,
  "seniority": <float>,
  "visa": <float>,
  "culture": <float>,
  "feasibility": <float>,
  "total": <float>,
  "rationale": "<string>"
}}
"""


def score_job(job: Job, client: OpenAI | None = None) -> Job:
    if client is None:
        client = OpenAI(api_key=env("OPENAI_API_KEY", required=True))

    prompt = _build_prompt(job)

    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": prompt},
        ],
        response_format={"type": "json_object"},
        temperature=0,
    )

    data = json.loads(response.choices[0].message.content)

    score = Score(
        role=float(data["role"]),
        stack=float(data["stack"]),
        seniority=float(data["seniority"]),
        visa=float(data["visa"]),
        culture=float(data["culture"]),
        total=float(data["total"]),
        rationale=data.get("rationale", ""),
    )

    updated = job.model_copy(update={"score": score, "status": Status.scored})
    log.info("scored %s @ %s → %.2f", job.title, job.company, score.total)
    return updated


def score_batch(jobs: list[Job], client: OpenAI | None = None) -> list[Job]:
    if client is None:
        client = OpenAI(api_key=env("OPENAI_API_KEY", required=True))

    cfg = load("config")
    max_calls = cfg["score"].get("max_llm_calls_per_run", 120)
    threshold = cfg["score"]["threshold"]

    results: list[Job] = []
    calls = 0

    for job in jobs:
        if calls >= max_calls:
            log.warning("hit max_llm_calls_per_run=%d, stopping early", max_calls)
            results.append(job)
            continue
        try:
            results.append(score_job(job, client=client))
            calls += 1
        except Exception as exc:
            log.error("failed to score %s @ %s: %s", job.title, job.company, exc)
            results.append(job.model_copy(update={"status": Status.failed, "fail_reason": str(exc)}))

    above = sum(1 for j in results if j.score and j.score.total >= threshold)
    log.info("scored %d jobs — %d above threshold %.1f", len(results), above, threshold)
    return results
