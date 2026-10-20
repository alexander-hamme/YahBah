"""Live probe of FieldMapper + match score with the EXAMPLE profile only (no personal.yaml, no DB writes).

Runs the real yahbah.llm.field_mapper code against the real local models, with config loaders
patched to config/prompts.yaml + config/personal.example.yaml and the answer cache stubbed out.
"""
import asyncio
import html
import json
import re
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import yaml

from yahbah.llm import field_mapper as fm
from yahbah.llm.field_mapper import FieldMapper, NeedsAnswer
from yahbah.schemas import FormField

ROOT = Path("/Users/alex/Documents/YahBah")
prompts = yaml.safe_load((ROOT / "config/prompts.yaml").read_text())
example = yaml.safe_load((ROOT / "config/personal.example.yaml").read_text())
config = {"prompts": {**prompts["prompts"], **example["prompts"]},
          "known_answers": example["known_answers"], "profile": example["profile"]}
fm.load_prompts_config = lambda: config

stored: dict = {}


async def _cached(label, options):
    return None


async def _store(label, options, answer):
    stored[label] = answer


fm._cached_answer, fm._store_answer = _cached, _store

p = example["profile"]
PROFILE = SimpleNamespace(**{k: p.get(k) for k in (
    "full_name", "email", "phone", "location", "linkedin_url", "github_url", "portfolio_url",
    "resume_path", "years_of_experience", "skills", "work_experience", "education", "bio")})

raw = (ROOT / "test/yahbah/fixtures/greenhouse_konux.html").read_text()
raw = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw, flags=re.S)
JD = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", raw))).strip()
JD = JD[: JD.find("Apply for this job")] if "Apply for this job" in JD else JD[:6000]

YN = ["Yes", "No"]
FIELDS = [
    ("1", "Are you legally authorized to work in Germany?", "radio", YN),
    ("2", "Will you now or in the future require visa sponsorship to work in the EU?", "radio", YN),
    ("3", "Are you authorized to work in the United States?", "radio", YN),
    ("4", "Do you require a visa to work in the US?", "radio", YN),
    ("5", "Are you at least 18 years of age?", "radio", YN),
    ("6", "Available from", "text", []),
    ("7", "Do you have 5+ years of professional experience with TypeScript and React?", "radio", YN),
    ("8", "Do you hold an active security clearance?", "radio", YN),
    ("9", "Describe a project where you deployed a backend service to AWS.", "textarea", []),
    ("10", "If yes, please provide details", "textarea", []),
]


async def probe_mapping() -> None:
    mapper = FieldMapper()
    orig = mapper._custom_answer
    prose_calls = []

    async def wrapped(f, ptext, jd, prior):
        orig_gen = mapper._prose.generate_structured

        async def capture(**kw):
            r = await orig_gen(**kw)
            prose_calls.append((f.key, r))
            return r
        mapper._prose.generate_structured = capture
        try:
            return await orig(f, ptext, jd, prior)
        except NeedsAnswer:
            return "<NEEDS_ANSWER>"
        finally:
            mapper._prose.generate_structured = orig_gen

    mapper._custom_answer = wrapped
    fields = [FormField(key=k, label=l, field_type=t, required=True, options=o) for k, l, t, o in FIELDS]
    t = time.time()
    out = await mapper.map(fields, PROFILE, job_description=JD)
    print(f"mapping done in {time.time() - t:.0f}s")
    for m in out:
        print(f"  [{m.key}] {m.label[:70]!r:72} source={m.source:22} conf={m.confidence:.2f} value={m.value[:90]!r}")
    for key, r in prose_calls:
        print(f"  prose[{key}] insufficient={r.insufficient_info} job_specific={r.job_specific} evidence={r.evidence}")
    print("  would cache:", stored)


async def probe_score(n: int) -> None:
    mapper = FieldMapper()
    summary = (f"Name: {p['full_name']}\nLocation: {p['location']}\nYears of experience: {p['years_of_experience']}\n"
               f"Skills: {', '.join(p['skills'])}\nBio: {p['bio']}\nWork experience:\n"
               + "\n".join(f"  - {e['title']} at {e['company']} ({e['duration']})" for e in p["work_experience"]))
    for label, jd in (("full JD", JD), ("first 400 chars (snippet-like)", JD[:400])):
        scores = []
        for _ in range(n):
            s, why = await mapper.compute_match_score(summary, jd)
            scores.append(s)
        print(f"match_score x{n} on {label}: {scores}")


async def main() -> None:
    what = sys.argv[1:] or ["map", "score"]
    if "map" in what:
        await probe_mapping()
    if "score" in what:
        await probe_score(5)


asyncio.run(main())
