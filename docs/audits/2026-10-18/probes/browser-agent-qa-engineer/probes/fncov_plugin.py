"""pytest plugin: record which src/yahbah functions start during the run (sys.monitoring PY_START)."""
import ast, json, sys
from pathlib import Path
SRC = "/Users/alex/Documents/YahBah/src/yahbah/"
hit = set()
TOOL = 4
def _start(code, offset):
    if code.co_filename.startswith(SRC):
        hit.add((code.co_filename[len(SRC):], code.co_firstlineno))
    else:
        return sys.monitoring.DISABLE
def pytest_configure(config):
    sys.monitoring.use_tool_id(TOOL, "fncov")
    sys.monitoring.register_callback(TOOL, sys.monitoring.events.PY_START, _start)
    sys.monitoring.set_events(TOOL, sys.monitoring.events.PY_START)
def pytest_unconfigure(config):
    sys.monitoring.set_events(TOOL, 0)
    rows = []
    for f in sorted(Path(SRC).rglob("*.py")):
        rel = str(f)[len(SRC):]
        tree = ast.parse(f.read_text())
        fns = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        lines = len(f.read_text().splitlines())
        # decorated functions: co_firstlineno is the first decorator line
        def first(n): return min([n.lineno] + [d.lineno for d in n.decorator_list])
        covered = [n.name for n in fns if (rel, first(n)) in hit or (rel, n.lineno) in hit]
        rows.append((rel, lines, len(fns), len(covered)))
    Path(sys.argv[0]).parent  # noqa
    out = Path("/private/tmp/claude-501/-Users-alex-Documents-YahBah/507601a9-f685-4b67-b6bc-a4c95c794a1d/scratchpad/browser-agent-qa-engineer/fncov.json")
    out.write_text(json.dumps(rows))
