"""
Per-run LLM logs: artifacts/<run_id>/llm/
  raw.jsonl       every raw Ollama reply (model, seconds, tokens, done_reason, thinking, content)
  steps/          browser-use's per-step prompt + parsed reply
  js_calls.jsonl  every inspect_page_js attempt

The current run's log dir lives in a contextvar, set once at the start of each
activity, so concurrent runs never write into each other's logs. All
artifacts/*/llm dirs together are capped at LOG_CAP_BYTES (oldest pruned first).
"""
import json
import time
from contextvars import ContextVar
from pathlib import Path

from loguru import logger

from yahbah.config import settings

LOG_CAP_BYTES = 250 * 1024 * 1024

_current: ContextVar[Path | None] = ContextVar("llm_log_dir", default=None)
_cap_warned = False
_total_bytes = 0  # running estimate of all LLM log bytes, recomputed on each prune


def _dir_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def _all_log_dirs() -> list[Path]:
    root = Path(settings.artifacts_dir)
    if not root.exists():
        return []
    dirs = [p / "llm" for p in root.iterdir() if (p / "llm").is_dir()]
    return sorted(dirs, key=lambda p: p.stat().st_mtime)


def _prune() -> None:
    global _total_bytes
    dirs = _all_log_dirs()
    total = sum(_dir_size(d) for d in dirs)
    while dirs and total > LOG_CAP_BYTES * 0.8:  # leave headroom for the new run
        oldest = dirs.pop(0)
        total -= _dir_size(oldest)
        for f in sorted(oldest.rglob("*"), reverse=True):
            f.unlink() if f.is_file() else f.rmdir()
        oldest.rmdir()
    _total_bytes = total


def use_run_log_dir(run_id: str) -> Path:
    """Point LLM logging for the current task at artifacts/<run_id>/llm/."""
    path = Path(settings.artifacts_dir) / run_id / "llm"
    if not path.exists():
        _prune()
        path.mkdir(parents=True)
    _current.set(path)
    return path


def current_log_dir() -> Path | None:
    return _current.get()


def append(filename: str, rec: dict) -> None:
    """Append a JSON record to the current run's log file; no-op outside a run."""
    global _cap_warned, _total_bytes
    log_dir = _current.get()
    if log_dir is None:
        return
    if _total_bytes >= LOG_CAP_BYTES:
        if not _cap_warned:
            logger.warning(f"LLM logs reached {LOG_CAP_BYTES >> 20} MB; no longer logging")
            _cap_warned = True
        return
    line = json.dumps({"t": time.strftime("%H:%M:%S"), **rec}, ensure_ascii=False) + "\n"
    with (log_dir / filename).open("a") as f:
        f.write(line)
    _total_bytes += len(line.encode())
