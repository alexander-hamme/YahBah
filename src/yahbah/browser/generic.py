"""
Generic LangGraph-based form extraction agent.

Replaces the Greenhouse-specific extractor for any site that isn't Greenhouse,
and also serves as a fallback when the Greenhouse extractor fails validation.

Agent loop (LangGraph StateGraph):
  snapshot_page → judge_form → [execute_action → snapshot_page]* → extract_fields

The loop continues until the LLM judges the real application form is visible,
or until MAX_ITERATIONS is reached.
"""
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, TypedDict

import yaml
from loguru import logger
from langgraph.graph import StateGraph, END
from playwright.async_api import Page

from yahbah.llm.client import OllamaClient
from yahbah.llm.page_analyzer import PageAnalyzer, FormJudgment
from yahbah.schemas import FormField, FormSchema
from yahbah.config import settings

MAX_ITERATIONS = 10
SITE_TEMPLATES_DIR = Path("config/site_templates")


# ---------------------------------------------------------------------------
# Agent state
# ---------------------------------------------------------------------------

class FormAgentState(TypedDict):
    page_snapshot: str
    visible_form_count: int
    iteration: int
    is_ready: bool
    judgment: dict | None          # last FormJudgment serialized
    actions_taken: list[str]
    # Populated once is_ready=True
    raw_fields: list[dict]
    job_description: str
    page_url: str
    page_title: str


# ---------------------------------------------------------------------------
# Site template loader
# ---------------------------------------------------------------------------

def load_site_template(ats_type: str) -> dict:
    path = SITE_TEMPLATES_DIR / f"{ats_type}.yaml"
    if not path.exists():
        path = SITE_TEMPLATES_DIR / "generic.yaml"
    with open(path) as f:
        return yaml.safe_load(f) or {}


def get_site_hints(ats_type: str) -> str:
    tmpl = load_site_template(ats_type)
    return tmpl.get("agent_hints", "No site-specific hints available.").strip()


# ---------------------------------------------------------------------------
# DOM helpers (run via Playwright)
# ---------------------------------------------------------------------------

async def _get_snapshot(page: Page) -> tuple[str, int]:
    """Return (accessibility_snapshot_str, visible_form_element_count)."""
    try:
        snapshot = await page.accessibility.snapshot()
        snapshot_str = json.dumps(snapshot, indent=2)[:8000]  # cap size
    except Exception:
        # Fallback: grab visible text
        snapshot_str = (await page.inner_text("body"))[:4000]

    # Count visible interactive form elements
    try:
        count = await page.evaluate("""() => {
            const sel = 'input:not([type=hidden]):not([type=submit]):not([type=button]),'
                      + 'select, textarea, [role=combobox], [role=radio], [role=checkbox]';
            return [...document.querySelectorAll(sel)]
                .filter(el => {
                    const r = el.getBoundingClientRect();
                    return r.width > 0 && r.height > 0;
                }).length;
        }""")
    except Exception:
        count = 0

    return snapshot_str, count


async def _extract_raw_fields(page: Page) -> list[dict]:
    """
    Extract raw form element info via Playwright DOM queries.
    Returns a compact list suitable for LLM analysis.
    """
    return await page.evaluate("""() => {
        function nearestLabel(el) {
            // 1. for= attribute pointing to this element
            if (el.id) {
                const lbl = document.querySelector(`label[for="${el.id}"]`);
                if (lbl) return lbl.innerText.trim();
            }
            // 2. ancestor label
            const ancestor = el.closest('label');
            if (ancestor) return ancestor.innerText.trim();
            // 3. aria-label / aria-labelledby
            if (el.getAttribute('aria-label')) return el.getAttribute('aria-label');
            const lblBy = el.getAttribute('aria-labelledby');
            if (lblBy) {
                const ref = document.getElementById(lblBy);
                if (ref) return ref.innerText.trim();
            }
            // 4. preceding sibling or parent text
            const parent = el.parentElement;
            if (parent) {
                const text = [...parent.childNodes]
                    .filter(n => n.nodeType === 3)
                    .map(n => n.textContent.trim())
                    .filter(t => t.length > 0)[0];
                if (text) return text;
            }
            return el.placeholder || el.name || '';
        }

        function getOptions(el) {
            if (el.tagName === 'SELECT') {
                return [...el.options].map(o => o.text.trim()).filter(Boolean);
            }
            return [];
        }

        const selectors = [
            'input:not([type=hidden]):not([type=submit]):not([type=button]):not([type=reset])',
            'select',
            'textarea',
        ];

        const els = [...document.querySelectorAll(selectors.join(','))];
        const visible = els.filter(el => {
            const r = el.getBoundingClientRect();
            return r.width > 0 && r.height > 0;
        });

        return visible.map((el, i) => ({
            index: i,
            tag: el.tagName.toLowerCase(),
            type: el.type || null,
            id: el.id || null,
            name: el.name || null,
            placeholder: el.placeholder || null,
            required: el.required || el.getAttribute('aria-required') === 'true',
            candidate_label: nearestLabel(el),
            options: getOptions(el),
        }));
    }""")


async def _get_page_text(page: Page) -> tuple[str, str, str]:
    """Return (title, url, body_text[:3000])."""
    title = await page.title()
    url = page.url
    try:
        body = await page.inner_text("body")
        body = body[:3000]
    except Exception:
        body = ""
    return title, url, body


# ---------------------------------------------------------------------------
# LangGraph node functions
# Note: LangGraph nodes must be synchronous or async coroutines.
# We pass page and analyzer via closure (graph is compiled per-run).
# ---------------------------------------------------------------------------

def make_snapshot_node(page: Page):
    async def snapshot_page(state: FormAgentState) -> dict:
        snapshot, count = await _get_snapshot(page)
        logger.debug(f"[generic-agent] snapshot: {count} visible form elements")
        return {"page_snapshot": snapshot, "visible_form_count": count}
    return snapshot_page


def make_judge_node(analyzer: PageAnalyzer, site_hints: str):
    async def judge_form(state: FormAgentState) -> dict:
        judgment: FormJudgment = await analyzer.judge_form(
            snapshot=state["page_snapshot"],
            site_hints=site_hints,
            actions_taken=state["actions_taken"],
            iteration=state["iteration"],
        )
        logger.info(
            f"[generic-agent] iter={state['iteration']} is_ready={judgment.is_ready} "
            f"reason={judgment.reasoning!r}"
        )
        if judgment.action:
            logger.info(f"[generic-agent] planned action: {judgment.action.type} "
                        f"selector={judgment.action.selector!r} — {judgment.action.description}")
        return {
            "is_ready": judgment.is_ready,
            "judgment": judgment.model_dump(),
        }
    return judge_form


def make_execute_node(page: Page):
    async def execute_action(state: FormAgentState) -> dict:
        judgment = state.get("judgment") or {}
        action = judgment.get("action")
        if not action:
            logger.warning("[generic-agent] execute_action called but no action in state")
            return {"iteration": state["iteration"] + 1}

        atype = action.get("type")
        selector = action.get("selector")
        text = action.get("text")
        desc = action.get("description", "")
        actions_taken = list(state["actions_taken"])
        actions_taken.append(f"[iter {state['iteration']}] {atype}: {selector or ''} — {desc}")

        try:
            if atype == "click" and selector:
                await page.locator(selector).first.click(timeout=10_000)
                await page.wait_for_load_state("domcontentloaded", timeout=15_000)
            elif atype == "wait" and selector:
                await page.wait_for_selector(selector, timeout=30_000)
            elif atype == "scroll":
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            elif atype == "type" and selector and text:
                await page.locator(selector).first.fill(text, timeout=10_000)
            else:
                logger.warning(f"[generic-agent] Unhandled action type or missing args: {action}")
        except Exception as exc:
            logger.warning(f"[generic-agent] Action failed ({atype} {selector}): {exc}")
            actions_taken[-1] += f" [FAILED: {exc}]"

        return {"iteration": state["iteration"] + 1, "actions_taken": actions_taken}
    return execute_action


def make_extract_node(page: Page, analyzer: PageAnalyzer, profile_keys: list[str]):
    async def extract_fields(state: FormAgentState) -> dict:
        logger.info("[generic-agent] Form is ready — extracting fields")
        raw_fields = await _extract_raw_fields(page)
        logger.info(f"[generic-agent] Raw DOM extraction: {len(raw_fields)} fields")

        title, url, body_text = await _get_page_text(page)

        # LLM enhancement — confirm labels and generate stable CSS selectors
        enhanced = []
        if raw_fields:
            try:
                enhanced = await analyzer.enhance_fields(raw_fields, profile_keys)
                logger.info(f"[generic-agent] LLM enhanced {len(enhanced)} fields")
            except Exception as exc:
                logger.warning(f"[generic-agent] Field enhancement failed: {exc} — using raw fields")

        return {
            "raw_fields": raw_fields,
            "job_description": body_text,
            "page_url": url,
            "page_title": title,
            "_enhanced_fields": [e.model_dump() for e in enhanced],
        }
    return extract_fields


# ---------------------------------------------------------------------------
# Graph routing
# ---------------------------------------------------------------------------

def route_after_judge(state: FormAgentState) -> str:
    if state["is_ready"]:
        return "extract_fields"
    if state["iteration"] >= MAX_ITERATIONS:
        logger.warning(f"[generic-agent] Max iterations ({MAX_ITERATIONS}) reached without finding form")
        return "extract_fields"  # try anyway rather than failing hard
    return "execute_action"


# ---------------------------------------------------------------------------
# GenericFormAgent — public interface
# ---------------------------------------------------------------------------

class GenericFormAgent:
    """
    Runs the LangGraph agent loop for a given page.
    Each instance is single-use (one run_id).
    """

    def __init__(
        self,
        ats_type: str = "generic",
        llm: OllamaClient | None = None,
        profile_keys: list[str] | None = None,
    ) -> None:
        self._ats_type = ats_type
        self._analyzer = PageAnalyzer(llm or OllamaClient())
        self._site_hints = get_site_hints(ats_type)
        self._profile_keys = profile_keys or []

    async def run(self, page: Page) -> tuple[FormSchema, str]:
        """
        Runs the agent loop on an already-open Playwright page.
        Returns (FormSchema, job_description).
        """
        # Build graph with page/analyzer bound via closures
        graph = StateGraph(FormAgentState)
        graph.add_node("snapshot_page", make_snapshot_node(page))
        graph.add_node("judge_form", make_judge_node(self._analyzer, self._site_hints))
        graph.add_node("execute_action", make_execute_node(page))
        graph.add_node("extract_fields", make_extract_node(
            page, self._analyzer, self._profile_keys
        ))

        graph.set_entry_point("snapshot_page")
        graph.add_edge("snapshot_page", "judge_form")
        graph.add_conditional_edges("judge_form", route_after_judge)
        graph.add_edge("execute_action", "snapshot_page")
        graph.add_edge("extract_fields", END)

        compiled = graph.compile()

        initial_state: FormAgentState = {
            "page_snapshot": "",
            "visible_form_count": 0,
            "iteration": 0,
            "is_ready": False,
            "judgment": None,
            "actions_taken": [],
            "raw_fields": [],
            "job_description": "",
            "page_url": page.url,
            "page_title": "",
        }

        final_state = await compiled.ainvoke(initial_state)

        return self._build_form_schema(final_state)

    def _build_form_schema(self, state: dict) -> tuple[FormSchema, str]:
        enhanced = state.get("_enhanced_fields") or []
        raw = state.get("raw_fields") or []

        fields: list[FormField] = []

        if enhanced:
            for ef in enhanced:
                if ef.get("skip"):
                    continue
                fields.append(FormField(
                    label=ef.get("label", ""),
                    field_type=ef.get("field_type", "text"),
                    css_selector=ef.get("css_selector"),
                    required=ef.get("required", False),
                    options=ef.get("options") or [],
                ))
        else:
            # Fallback: build fields from raw DOM data if LLM enhancement failed
            for rf in raw:
                label = rf.get("candidate_label") or rf.get("placeholder") or rf.get("name") or ""
                if not label:
                    continue
                selector = None
                if rf.get("id"):
                    selector = f"#{rf['id']}"
                elif rf.get("name"):
                    selector = f"[name={json.dumps(rf['name'])}]"

                fields.append(FormField(
                    label=label,
                    field_type=rf.get("type") or rf.get("tag") or "text",
                    element_id=rf.get("id"),
                    name=rf.get("name"),
                    placeholder=rf.get("placeholder"),
                    required=rf.get("required", False),
                    options=rf.get("options") or [],
                    css_selector=selector,
                ))

        schema = FormSchema(
            fields=fields,
            page_url=state.get("page_url", ""),
            page_title=state.get("page_title", ""),
        )
        job_description = state.get("job_description", "")
        logger.info(
            f"[generic-agent] Built FormSchema: {len(fields)} fields, "
            f"url={schema.page_url!r}"
        )
        return schema, job_description
