"""Where did each prose answer's evidence come from? Prints only counts/lengths (no content)."""
import json, re, sys, html
from pathlib import Path

def norm(s): return re.sub(r"[^a-z0-9]+", " ", s.casefold()).strip()
jd_html = Path("test/yahbah/fixtures/greenhouse_konux.html").read_text()
jd = norm(html.unescape(re.sub(r"<[^>]+>", " ", jd_html)))
for f in sorted(Path("artifacts").glob("debug-*/llm/raw.jsonl")):
    for line in f.open():
        r = json.loads(line)
        if r.get("role") != "prose": continue
        try: c = json.loads(r["content"])
        except Exception: continue
        ev = c.get("evidence")
        print(f.parent.parent.name, "anslen=", len(c.get("answer","")), "insufficient=", c.get("insufficient_info"),
              "job_specific=", c.get("job_specific"),
              "evidence=", None if ev is None else [(len(norm(q)), norm(q) in jd) for q in ev])
