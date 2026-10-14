"""
Phase 0 spike: can Browser Use act as our element-discovery/action engine
on a Chromium that Playwright launched and still controls?

Usage:
  uv run python scripts/spike_browser_use.py URL [URL ...] [--llm] [--click-apply]

Checks per URL (each URL gets its own Chromium on its own CDP port, run concurrently):
  1. Playwright launches Chromium with a CDP port; BrowserSession attaches to the same tab.
  2. get_browser_state_summary() → element count, frames, llm_representation, screenshot.
  3. backend_node_id stability across two snapshots.
  4. Playwright screenshot of the same page still works.
  5. (--click-apply) execute a Browser Use "click" on the first Apply-like element without the Agent.
  6. (--llm) one llama4 judge call with screenshot + element text + JSON schema; latency + validity.
"""
import os

# Must be set before browser_use is imported: no telemetry / cloud sync of personal data.
os.environ.setdefault("ANONYMIZED_TELEMETRY", "false")
os.environ.setdefault("BROWSER_USE_CLOUD_SYNC", "false")
os.environ.setdefault("BROWSER_USE_LOGGING_LEVEL", "warning")

import argparse
import asyncio
import base64
import json
import socket
import time
from pathlib import Path
from typing import Literal

import httpx
from browser_use import BrowserSession, Tools
from playwright.async_api import async_playwright
from pydantic import BaseModel

OUT_DIR = Path("artifacts/_spike")
OLLAMA_URL = "http://localhost:11434/api/chat"
VISION_MODEL = "llama4:latest"


class Judgment(BaseModel):
    # reasoning first so the model "thinks" before committing to a state
    reasoning: str
    state: Literal["need_action", "form_ready", "blocked"]
    action: Literal["click", "fill", "scroll", "wait", "none"]
    element_index: int | None = None
    text: str | None = None

    def consistent(self) -> bool:
        if self.state == "need_action":
            return self.action != "none" and (self.action in ("scroll", "wait") or self.element_index is not None)
        return self.action == "none"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


JUDGE_SYSTEM = (
    "You control a web browser whose goal is to reach a job application form.\n"
    "Interactive elements are listed as [index]<tag attributes />text; only these indices are valid.\n"
    "Decide the page state:\n"
    "- form_ready: the application's input fields (name, email, resume, ...) are present. action=none.\n"
    "- need_action: choose ONE action that moves toward the form, e.g. click an 'Apply' button or "
    "an 'Application' tab, accept a cookie banner. Give element_index for click/fill.\n"
    "- blocked: the job is closed, a CAPTCHA/security check, or a login you cannot pass. action=none, "
    "explain in reasoning.\n"
    "Respond with JSON only."
)


async def judge(
    element_text: str, screenshot_b64: str | None, model: str, keep_alive: str
) -> tuple[Judgment | None, float, str]:
    msg = {"role": "user", "content": f"Elements:\n{element_text[:12000]}"}
    if screenshot_b64:
        msg["images"] = [screenshot_b64]
    t0 = time.perf_counter()
    async with httpx.AsyncClient(timeout=900) as client:
        r = await client.post(OLLAMA_URL, json={
            "model": model,
            "messages": [{"role": "system", "content": JUDGE_SYSTEM}, msg],
            "format": Judgment.model_json_schema(),
            "stream": False,
            "think": False,
            "keep_alive": keep_alive,
            "options": {"temperature": 0.1},
        })
    dt = time.perf_counter() - t0
    raw = r.json().get("message", {}).get("content", "")
    try:
        return Judgment.model_validate_json(raw), dt, raw
    except Exception:
        return None, dt, raw


async def probe(url: str, tag: str, use_llm: bool, click_apply: bool) -> dict:
    out: dict = {"url": url}
    port = free_port()
    cdp_url = f"http://127.0.0.1:{port}"
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=[f"--remote-debugging-port={port}"])
        context = await browser.new_context(viewport={"width": 1280, "height": 900})
        page = await context.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        try:
            await page.wait_for_load_state("networkidle", timeout=5_000)
        except Exception:
            pass

        session = BrowserSession(cdp_url=cdp_url, keep_alive=True, headless=True)
        await session.start()
        try:
            t0 = time.perf_counter()
            state = await session.get_browser_state_summary(include_screenshot=True)
            out["state_seconds"] = round(time.perf_counter() - t0, 2)
            smap = state.dom_state.selector_map
            text = state.dom_state.llm_representation()
            out["bu_url"] = state.url
            out["pw_url"] = page.url
            out["same_tab"] = state.url.split("#")[0] == page.url.split("#")[0]
            out["elements"] = len(smap)
            out["frames"] = len({n.frame_id for n in smap.values()})
            out["inputs"] = sum(1 for n in smap.values() if n.tag_name in ("input", "select", "textarea"))
            out["llm_text_chars"] = len(text)
            out["state_error"] = state.state_error

            OUT_DIR.mkdir(parents=True, exist_ok=True)
            (OUT_DIR / f"{tag}_elements.txt").write_text(text)
            if state.screenshot:
                (OUT_DIR / f"{tag}_bu_highlighted.png").write_bytes(base64.b64decode(state.screenshot))
            await page.screenshot(path=str(OUT_DIR / f"{tag}_playwright.png"))
            out["playwright_screenshot"] = True

            # Identity stability: same backend_node_ids on a second snapshot?
            ids1 = {n.backend_node_id for n in smap.values()}
            state2 = await session.get_browser_state_summary(include_screenshot=False)
            ids2 = {n.backend_node_id for n in state2.dom_state.selector_map.values()}
            out["id_overlap"] = round(len(ids1 & ids2) / max(len(ids1), 1), 2)

            if click_apply:
                apply_idx = next(
                    (i for i, n in state2.dom_state.selector_map.items()
                     if "apply" in (n.get_all_children_text() or "").lower()
                     or "apply" in (n.attributes.get("aria-label", "") or "").lower()),
                    None,
                )
                out["apply_index"] = apply_idx
                if apply_idx is not None:
                    tools = Tools()
                    res = await tools.registry.execute_action(
                        "click", {"index": apply_idx}, browser_session=session
                    )
                    await asyncio.sleep(3)
                    out["click_error"] = getattr(res, "error", None)
                    after = await session.get_browser_state_summary(include_screenshot=True)
                    out["after_click_url"] = after.url
                    out["after_click_inputs"] = sum(
                        1 for n in after.dom_state.selector_map.values()
                        if n.tag_name in ("input", "select", "textarea")
                    )
                    (OUT_DIR / f"{tag}_after_click.txt").write_text(after.dom_state.llm_representation())
                    out["pw_url_after_click"] = page.url
                    out["pw_pages"] = len(context.pages)

            if use_llm:
                out["_text"] = text
                out["_screenshot"] = state.screenshot
        except Exception as exc:
            out["exception"] = f"{type(exc).__name__}: {exc}"
        finally:
            await session.stop()
            await browser.close()
    return out


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("urls", nargs="+")
    ap.add_argument("--llm", action="store_true")
    ap.add_argument("--click-apply", action="store_true")
    ap.add_argument("--models", default="llama4:latest",
                    help="comma-separated; suffix ':text' to omit the screenshot, e.g. qwen3-coder-next:latest:text")
    ap.add_argument("--keep-alive", default="30m")
    args = ap.parse_args()

    results = await asyncio.gather(
        *(probe(u, f"site{i}", args.llm, args.click_apply) for i, u in enumerate(args.urls)),
        return_exceptions=True,
    )
    for r in results:
        if isinstance(r, dict):
            print(json.dumps({k: v for k, v in r.items() if not k.startswith("_")}, indent=2, default=str))
        else:
            print(json.dumps({"exception": repr(r)}))

    if not args.llm:
        return
    # Judge sequentially (Ollama serializes anyway) so latencies are per call, not queueing.
    for spec in args.models.split(","):
        text_only = spec.endswith(":text")
        model = spec.removesuffix(":text")
        print(f"\n=== {model} ({'text only' if text_only else 'text + screenshot'}) ===")
        for r in results:
            if not isinstance(r, dict) or "_text" not in r:
                continue
            j, dt, raw = await judge(r["_text"], None if text_only else r["_screenshot"], model, args.keep_alive)
            verdict = "INVALID" if j is None else ("ok" if j.consistent() else "INCONSISTENT")
            print(f"{dt:6.1f}s  {verdict:12}  {r['url'][:60]}")
            print(f"         {j.model_dump() if j else raw[:300]}")


if __name__ == "__main__":
    asyncio.run(main())
