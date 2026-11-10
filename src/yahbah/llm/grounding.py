"""
Code-side grounding checks on model-written answers.

- `unsupported_numbers(answer, material)`: numbers an answer states that the material doesn't. A
  generated answer may not introduce a metric, year, duration or count, so its numbers must appear
  in the applicant facts (never the job description). Digits inside names (S3, K8s, Web3) are not
  numbers here.
- `drop_sentences(text, numbers)`: the text without the sentences that state those numbers.
"""
import re

_NUMBER_RE = re.compile(r"(?<![A-Za-z\d])\d[\d,.]*")


def numbers(text: str) -> set[str]:
    """The numbers a text states, normalized ("$150,000" -> "150000", "3.5x" -> "3.5", "2B" -> "2")."""
    out = set()
    for raw in _NUMBER_RE.findall(text):
        n = raw.rstrip(".,").replace(",", "")
        if n:
            out.add(n.rstrip("0").rstrip(".") if "." in n else n)
    return out


def unsupported_numbers(answer: str, *material: str) -> set[str]:
    allowed = set().union(*(numbers(m) for m in material)) if material else set()
    return numbers(answer) - allowed


def drop_sentences(text: str, unsupported: set[str]) -> str:
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    return " ".join(s for s in sentences if not (numbers(s) & unsupported)).strip()
