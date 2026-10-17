"""
browser-use model clients and agent runs.

- chat_model(role): a ChatOllama for a role from yahbah.llm.models, logging every
  raw reply to the current run's llm/raw.jsonl.
- Crew: who does what in an agent run (driver, vision, escalation + tools + stall tracker).
- Escalator: on a stall (or repeated failures) hands the next steps to the escalation
  model with thinking + screenshots, then hands back to the driver.
- run_agent: a browser-use Agent configured for slow local models.
"""
import time
from dataclasses import dataclass

from browser_use import Agent, ChatOllama
from browser_use.browser import BrowserSession
from loguru import logger

from yahbah.agent.tools import StallTracker, make_tools
from yahbah.config import settings
from yahbah.llm import logs
from yahbah.llm.models import Role, ollama_options, spec


@dataclass
class LoggingChatOllama(ChatOllama):
    """ChatOllama that appends every raw Ollama reply (before browser-use parses it) to raw.jsonl."""

    role: str = "driver"

    def get_client(self):
        client = super().get_client()
        chat = client.chat
        role, model = self.role, self.model

        async def logged_chat(**kw):
            started = time.time()
            try:
                res = await chat(**kw)
            except Exception as exc:
                logs.append("raw.jsonl", {"role": role, "model": model, "error": repr(exc),
                                          "secs": round(time.time() - started, 1)})
                raise
            logs.append("raw.jsonl", {
                "role": role,
                "model": model,
                "think": kw.get("think"),
                "secs": round(time.time() - started, 1),
                "prompt_tokens": res.prompt_eval_count,
                "output_tokens": res.eval_count,
                "done_reason": res.done_reason,
                "images": sum(
                    len(m.get("images") or []) if isinstance(m, dict) else len(getattr(m, "images", None) or [])
                    for m in kw.get("messages", [])
                ),
                "thinking": res.message.thinking,
                "content": res.message.content,
            })
            return res

        client.chat = logged_chat
        return client


def chat_model(role: Role) -> LoggingChatOllama:
    return LoggingChatOllama(
        model=spec(role).model,
        host=settings.ollama_base_url,
        timeout=settings.ollama_timeout,
        ollama_options=ollama_options(role),
        role=role,
    )


@dataclass
class Crew:
    """Who does what in an agent run."""

    driver: LoggingChatOllama
    vision: LoggingChatOllama
    escalation: LoggingChatOllama
    tools: object
    stalls: StallTracker
    escalate_after_failures: int = 2  # plus StallTracker.stalled (repeats / same target / unchanged page)
    escalated_steps: int = 2


def make_crew(fill_ctx=None) -> Crew:
    """fill_ctx (a fill.FillContext): also give the agent the key-based fill tools (fix phase).
    Never a submit tool."""
    vision = chat_model("vision")
    tools, stalls = make_tools(vision_llm=vision, fill_ctx=fill_ctx)
    return Crew(
        driver=chat_model("driver"),
        vision=vision,
        escalation=chat_model("escalation"),
        tools=tools,
        stalls=stalls,
    )


class Escalator:
    """Step callback: hands the next steps to the escalation model when the agent stalls, then hands back.
    browser-use reads agent.llm and settings.use_vision fresh every step, so swapping them is safe."""

    def __init__(self, crew: Crew) -> None:
        self.crew = crew
        self.agent: Agent | None = None
        self.remaining = 0
        self.escalations = 0

    def on_step(self, state, model_output, n_steps) -> None:
        self.crew.stalls.on_step(state, model_output, n_steps)
        a, s = self.agent, self.crew.stalls
        if a is None:
            return
        if self.remaining:
            self.remaining -= 1
            if not self.remaining:
                a.llm, a.settings.use_vision = self.crew.driver, False
                s.reset()  # judge the driver's progress afresh
                logger.info(f"[escalation] step {n_steps}: back to {self.crew.driver.model}")
            return
        failures = a.state.consecutive_failures
        if s.stalled or failures >= self.crew.escalate_after_failures:
            a.llm, a.settings.use_vision = self.crew.escalation, True
            self.remaining = self.crew.escalated_steps
            self.escalations += 1
            logger.info(
                f"[escalation] step {n_steps}: repeats={s.repeats} same-target={s.target_repeats} "
                f"unchanged={s.unchanged} failures={failures} -> {self.crew.escalation.model} "
                f"(thinking + screenshot) for {self.remaining} steps"
            )


async def run_agent(task: str, crew: Crew, session: BrowserSession, max_steps: int, output=None):
    """Run a browser-use agent on the session's current page. Returns the AgentHistoryList."""
    esc = Escalator(crew)
    log_dir = logs.current_log_dir()
    agent = Agent(
        task=task,
        llm=crew.driver,
        tools=crew.tools,
        register_new_step_callback=esc.on_step,
        browser_session=session,
        directly_open_url=False,
        use_vision=False,  # the driver is text-only; screenshots go via look_at_screen or escalation
        page_extraction_llm=crew.driver,
        output_model_schema=output,
        use_judge=False,  # browser-use's grading pass would double every run's cost
        step_timeout=settings.ollama_timeout,
        llm_timeout=settings.ollama_timeout,
        max_actions_per_step=3,
        save_conversation_path=(log_dir / "steps") if log_dir else None,
    )
    esc.agent = agent
    history = await agent.run(max_steps=max_steps)
    logger.info(f"[agent] {history.number_of_steps()} steps, {esc.escalations} escalation(s)")
    return history
