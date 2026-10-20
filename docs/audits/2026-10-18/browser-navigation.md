# Browser Navigation Audit — browser-navigation-engineer

- **Date:** 2026-10-18
- **Agent:** browser-navigation-engineer (Claude Code development agent)
- **Revision examined:** branch `browser-use` @ `2911263`, plus uncommitted `CLAUDE.md` edit (no source changes)
- **Scope:** Job URL → actionable application form: discover tiers, form-ready detection, board profiles, browser-use 0.13.11 integration, session lifecycle, navigation observability.
- **Status:** Historical point-in-time audit. Findings may become outdated; the wiki and source are authoritative.

## Summary
The navigation layer is built the right way. It runs a code → one-shot → agent ladder, checks each tier's result in code, gives agents named read-only tools instead of `evaluate`, and starts one isolated Chrome profile per run. The per-step escalation also works: I checked it against the installed 0.13.11 `Agent`, which re-reads `agent.llm` and `settings.use_vision` every step (`browser_use/agent/service.py:1144`, callback at `:1712`). The weak point is the one gate everything depends on. `looks_like_form`, run over a reader that only sees the top frame, lets in too much: search bars, a hidden file input and account-creation pages all pass as "the application form" with no model involved. It also misses too much: forms in any iframe other than a Greenhouse embed, or in shadow DOM, can never pass. Real-world evidence for the new pipeline is 4 runs on a single Greenhouse page. Meanwhile 100% (302/302) of the jobs the Gmail discovery pipeline queues are jobright.ai aggregator pages, and no version of YahBah has ever navigated past one. Workday-class portals are not ready. The most likely path today ends in a false "form-ready" on Workday's Create Account page, there is no account capability (`generate_password` is never called), and multi-step wizards depend on the forms layer.

## Findings

### F1. Form-ready detection accepts search bars, unrelated file inputs, and account-creation pages as the application form
- **Priority:** High (the worst case borders on Critical; see the failure scenario)
- **Confidence:** Confirmed defect (reproduced offline)
- **Issue or opportunity:** `looks_like_form` (`src/yahbah/agent/discover.py:153-156`) accepts a page if it has at least 3 visible non-checkbox/radio controls anywhere, or any file input. `READ_FORM_SCHEMA_JS` skips the visibility check for file inputs (`src/yahbah/agent/tools.py:82`) and counts `password` inputs as ordinary fields. Controls are not grouped by form or container. There is no check for applicant-identity or job signals, and `role=search` forms are not excluded. This is the only "is this an application form?" check in the whole pipeline:
  - Discover tier 1 returns on it before it ever looks for Apply (`discover.py:283-285`).
  - `map_form`'s `schema_trustworthy` reuses it (`discover.py:159-160`).
  - Nothing downstream checks again.
- **Evidence:** An offline probe ran the real `discover()` and `map_form()` against locally served pages with the model tiers stubbed out. No model was called in any of these cases:

  | Page | `discover()` | `map_form` (code tier) | `FIND_SUBMIT_JS` would tag |
  |---|---|---|---|
  | Job description + header job search (keyword, location, department `<select>`); real form behind an "Apply" link | form-ready at tier 1: "form already on page (3 fields)" | 3 fields accepted | "Search" |
  | Job description + hidden `<input type=file>` in a "match my résumé" widget | form-ready at tier 1 (1 field) | 1 field (`resume_match`) | "Upload your resume to see matching jobs" |
  | Workday-style Create Account (email, password, verify password, consent checkbox) | form-ready at tier 1 | 4 fields, incl. 2 × `password` | "Create Account" |

  When nothing else matches, the submit locator picks "the only submit button in scope" (`src/yahbah/agent/submit.py:68`). So it selects the button of whatever form was wrongly accepted.
- **Impact:**
  - **Career sites:** many have a header job search or a talent-community / "upload your résumé" widget. On those, YahBah never clicks Apply. It maps, fills and gates the wrong form.
  - **Workday (the named next target):** Apply → "Apply Manually" → Create Account would be accepted as the application form instead of being reported as an account wall. Nothing supplies a password (`generate_password`, `src/yahbah/credentials.py:88`, is never called). The best case is a misclassified `needs_answer` that asks the user for a "Password" answer.
  - **Worst case (not run end to end in this audit):** applicant data is submitted to a non-application form (talent community, résumé-match widget) and `submit_attempted_at` is set. If the post-submit verdict says "submitted", the COMPLETED run marks that canonical URL as a duplicate, which blocks the real application.
- **Proposed resolution:** Replace `looks_like_form` with one page classifier in code, shared by discover and map:
  - Group controls by their containing `<form>` (or nearest common container).
  - Score the largest group for applicant-identity evidence: the autocomplete tokens the reader already collects (`given-name`, `family-name`, `email`, `tel`), name/email/phone/résumé labels, and a *visible* file control or its labelled button.
  - Return `application_form | account_wall | search | none`.
  - A visible password field means `account_wall`, which raises `StageFailed("login_wall")` in code.
  - `role=search`, `type=search` or search-only label sets mean `search`, not a form.
  - Groups with weak evidence (e.g. name + email + file but no job context) go to a one-shot yes/no question that includes the job title, checked in code.
  - Expose the winning group's container so the forms engineer can scope the submit locator to it.
- **Failure scenario & validating test:** Add the three pages above plus a talent-community page as discover fixtures (served locally, models stubbed). Assert that:
  - tier 1 accepts none of them;
  - the search-bar page goes on to the Apply link and the real form;
  - Create Account raises `login_wall` with zero model calls;
  - `greenhouse_konux` still classifies as `application_form`.

### F2. The discovery pipeline's entire real input (jobright.ai aggregator pages) has no resolution path and has never been navigated
- **Priority:** High
- **Confidence:** Strongly supported risk. The input distribution and the lack of any tested path are confirmed. How the new generic ladder behaves on live jobright pages is unverified, because this audit had no live access.
- **Issue or opportunity:**
  - `BoardProfile.resolve_application_url` exists and discover calls it (`discover.py:273-280`), but no profile sets it (`src/yahbah/agent/boards.py:41-43, 102`).
  - There is no jobright profile, fixture or test.
  - Only models detect closed postings, and by design the one-shot's `job_closed` claim is not trusted (`discover.py:8-9, 253-255`). So a closed posting costs the one-shot plus the 6-step navigation agent (`discover.py:299-302`).
- **Evidence** (read-only DB aggregates through the app's own config; no PII):
  - **`scheduled_job`:** 302/302 rows are `jobright.ai`: 300 `/jobs/info/<id>` and 2 `/jobs/recommend` (probably listing pages). 301 were promoted.
  - **`application_run`:** 301/338 are jobright URLs, and all FAILED at `EXTRACT_FORM`. In every failure message the final URL host is still `jobright.ai`. That was the pre-agentic, Greenhouse-only validator (`archive/pre-agentic:src/yahbah/browser/greenhouse.py:256-292`), so it says nothing about how the new ladder would do. It does mean jobright → ATS has never worked in any version.
  - **Closed at ingestion:** 186/302 (62%) of the jobright page texts stored at ingestion (`scheduled_job.description`) contain "…has closed. APPLY to similar jobs".
  - **`artifacts/_observations.jsonl`:** 4 rows, all for the same Greenhouse page. The new pipeline has no runs on any other host.
- **Impact:**
  - The "discover → evaluate → apply" vision has zero demonstrated end-to-end yield today.
  - Under the new pipeline, every promoted jobright job would pay for local-model tiers (7–150 s per call, `src/yahbah/workflows/application.py:67`) on a page whose real form lives on another site, and 62% of those jobs are already closed.
  - Without resolving to the employer's ATS URL, board detection, cross-source dedup and match scoring all run against aggregator content.
- **Proposed resolution:**
  1. Capture 2–3 jobright fixtures: at least one open and one closed posting. The user runs `scripts/save_fixture.py` while logged out, so the pages carry minimal PII. Design against the real DOM rather than guessing its structure.
  2. Add a `JOBRIGHT` BoardProfile:
     - a `resolve_application_url` that reads the outbound link to the original posting, or embedded page data, deterministically;
     - a new optional `closed_js` hook, so closed postings raise `job_closed` in code before any model tier.

     Two cheap generic versions of closed detection: a JSON-LD `validThrough` date in the past, and a main-document HTTP 404/410 → `job_closed`.
  3. Expose the resolver as a reusable function with a small typed result (`resolved_url`, `board`, `closed`, `evidence`). Ingestion can then resolve before admission, so closed jobs are never queued and the canonical URL is the employer's ATS. The browser side owns the resolver; gmail-job-discovery-engineer owns calling it.
  4. Keep the generic fallback as it is. If the CTA opens a new tab, `click_node` already switches to it (`discover.py:206-215`; confirmed in the probe, where `newtab.html` reached the form at tier 1) and `_detect` runs again.
- **Failure scenario & validating test:** An offline test on the saved fixtures: with zero model calls, the resolver returns the ATS URL for the open posting and `closed=True` for the closed one, and `discover()` on the closed fixture raises `job_closed` from code.

### F3. Code readers and verification only see the top document: iframe-embedded ATS forms (other than Greenhouse) and shadow-DOM forms can never pass discover
- **Priority:** High
- **Confidence:** Confirmed defect (reproduced offline)
- **Issue or opportunity:**
  - `run_js` evaluates in the focused target's main frame (`tools.py:153-168`), and `READ_FORM_SCHEMA_JS` uses `document.querySelectorAll` (`tools.py:76`), which crosses neither iframe nor shadow-root boundaries.
  - The only frame handling is `_follow_embed`, which matches Greenhouse's embed URL pattern only (`boards.py:71, 124-131`).
  - browser-use 0.13.11 itself walks same-origin and cross-origin iframes (`cross_origin_iframes=True` by default, `browser_use/browser/profile.py:675`) and shadow DOM. So its agent can see and reach these forms, but our acceptance check cannot.
- **Evidence:** Probe with the model tiers stubbed: a job page plus a 4-field application form (first name, last name, email, résumé), embedded three ways:

  | Embedding | Our reader | browser-use `selector_map` | `discover()` |
  |---|---|---|---|
  | same-origin `<iframe>` | 0 fields | 4 controls | one-shot → agent → `form_not_found` |
  | cross-origin `<iframe>` (127.0.0.1 → localhost) | 0 | 4 | same |
  | open shadow root (custom element) | 0 | 4 | same |

  Even if one of these forms were accepted, map, fill and submit all use the same top-frame `run_js` and `[data-ja-field]` lookups. And these page types can't even be saved as fixtures today: `scripts/save_fixture.py:40,43` saves the top frame's `outerHTML` with scripts stripped.
- **Impact:** Company career sites that embed iCIMS, Ashby, Jobvite, Workable, BambooHR and similar ATSs in an iframe, and forms built from web components, all fail after spending both model tiers (plus the retry; see F4). This is a whole category of sites, not one site, which runs against "general mechanisms over board-specific exceptions".
- **Proposed resolution:**
  1. Generalize `_follow_embed`:
     - List the frames: CDP `Page.getFrameTree`, plus `BrowserSession.get_all_frames()` for out-of-process iframes.
     - Run the schema reader in each frame, in an isolated world per `frameId`.
     - If a child frame holds the best application-form group (using the F1 classifier), navigate the tab to that frame's URL at top level and check again.
     - Keep Greenhouse's pattern as a fast path.

     This keeps every downstream stage working on the top frame and needs no refactor across teams.
  2. For shadow DOM, add a shared deep-query JS prelude, used by the schema reader and by every `[data-ja-field]` lookup. That lookup is the element-targeting contract shared with fill and submit, so coordinate with application-forms-engineer.
  3. Make `run_js` frame-aware only if (1) turns out to be insufficient, e.g. for frames that refuse to load at top level.
- **Failure scenario & validating test:** Locally served fixtures for the same-origin iframe, cross-origin iframe and open shadow root. Assert that discover reaches form-ready in code and that `map_form` returns the 4 fields. The probe script below is a ready template.

### F4. Discover failures are invisible to observation, partly unclassified, and retried in full
- **Priority:** Medium
- **Confidence:** Confirmed defect (traced in code)
- **Issue or opportunity:**
  - **Observation log:** `record_observation` is only called after a successful map (`src/yahbah/workflows/activities/browser.py:225`). Every discover or map failure is therefore missing from `_observations.jsonl`, even though that log exists to show where unknown boards fail (`boards.py:15-16`).
  - **Unexpected exceptions:** `discover_activity` catches only `StageFailed` (`browser.py:172`). Any browser-use or CDP exception leaves no screenshot, no trace row and no reason code. Examples are a click or navigation timeout, or the raw `RuntimeError: Session with given id not found` that the probe triggered by closing the focused tab.
  - **Retries:** `form_not_found` and `form_unmapped` are not in `_FINAL` (`browser.py:56-57`), and the workflow retries discover/map once (`application.py:69, 86-89`). A retry reopens the URL and runs the whole ladder again. That doubles the model cost of every unreachable form, every closed job the model labels "other", and every iframe form (F3).
  - **Tests:** discover's only coverage is `detect_board` on one Greenhouse fixture (`test/yahbah/agent/test_fixtures.py`). Nothing tests the ladder, new tabs, embeds, closed jobs, login walls or false positives.
- **Evidence:** The code references above. Also, `_observations.jsonl` holds 4 rows and all are successes.
- **Impact:** We can't measure navigation success rate, false-positive rate, or failures by host or board. Every scenario in this audit had to be built by hand.
- **Proposed resolution:**
  - Record an observation from discover and map on every exit (success, `StageFailed`, unexpected exception), with the final URL host, board, reason and the tiers reached.
  - On unexpected exceptions, save a screenshot and the trace, then re-raise as a `browser_error` type that stays retryable.
  - Make "ladder exhausted" outcomes final, and retry only infrastructure errors. The retry semantics need temporal-platform-engineer.
  - Add a discover-ladder test module that stubs the model tiers and uses locally served pages (the probe pattern). Cover success at each tier, new tab, closed posting, login wall, iframe/shadow, and the F1 false positives.
- **Failure scenario & validating test:** A stubbed-model test where `discover()` raises a non-`StageFailed` exception should produce an observation row, a screenshot and a `browser_error` type. A ladder-exhausted `form_not_found` should not be retried.

### F5. Tiers 1–2 are narrower than they look, so unfamiliar sites fall through to the agent loop
- **Priority:** Medium
- **Confidence:** Confirmed defect for the mechanism (reproduced offline). How often it happens on real sites is a hypothesis to measure once F4 lands.
- **Issue or opportunity:**
  - The one-shot tier receives `llm_representation()[:40_000]` (`discover.py:247`) and can only click. browser-use only indexes elements within the viewport plus 1000 px (`browser_use/dom/service.py:64, 342-343`), and BrowserProfile does not expose that limit.
  - Readiness is handled with fixed sleeps, checked once each: 3 s in `SessionRegistry.open` (`src/yahbah/agent/session.py:55`) and `settle(3)` after clicks (`discover.py:149-150, 212`).
- **Evidence:**
  - **Long page:** the CTA sits about 1.6 viewports down and reads "Apply for this role →". `FIND_APPLY_JS` misses it because its regex must match the element's whole text (`discover.py:46`). The one-shot's input then contains only the two header links ("Home", "Teams"), so the model can only pick a wrong link or answer "unknown".
  - **Slow SPA:** content that renders at 4.5 s makes the code tier miss ("no apply button found"), and the run goes straight to the one-shot and agent tiers.
  - **Slow form:** a form that rendered 3.5 s after the click was found only because the embed check and schema read happened to take about another second.
- **Impact:** Many model calls go to pages that code could handle. Local calls take 7–150 s each, and the agent tier is 6 steps plus escalation to the escalation model. The one-shot can also wander off the job page.
- **Proposed resolution:**
  - Build the one-shot's candidates in code from the whole document: visible links and buttons with their text, aria label and href, deduplicated and tagged with `data-ja-field`, plus any closed-notice text. Stop using the viewport-limited serialization.
  - Replace the fixed sleeps with a bounded wait for a condition: form classified, CTA found, or DOM quiet for about 500 ms. Implement it as a MutationObserver promise run through `run_js` (which already sets `awaitPromise`), capped at about 10–15 s.
  - Loosen the CTA match to a scored prefix match with exclusions such as "similar jobs" and "filters". The result is still verified in code after the click.
- **Failure scenario & validating test:** Add the long-page and slow-SPA pages as discover fixtures and assert form-ready with zero model calls.

**Probe material.** The probe material is in this session's scratchpad. It is ephemeral, so copy it before relying on it.
- Directory: `/private/tmp/claude-501/-Users-alex-Documents-YahBah/507601a9-f685-4b67-b6bc-a4c95c794a1d/scratchpad/browser-navigation-engineer/`
- `probe_discover.py`: the real `discover()` with the model tiers and `load_prompts_config` stubbed; personal config is never loaded.
- `probe_chain.py`: the `map_form` code tier plus `FIND_SUBMIT_JS` tagging; nothing is clicked.
- `probe_oneshot_input.py`: checks what the one-shot tier receives.
- `site/`: the synthetic pages.

## Documentation discrepancies
- `docs/wiki/modules/agent.md:19` and `src/yahbah/agent/boards.py:15` say "Every run appends to `_observations.jsonl`". In fact only runs that get through `map_form` do (`browser.py:225`).
- `docs/wiki/modules/agent.md:18` says `run_agent` uses `settings.agent_max_steps`. That setting (`src/yahbah/config.py:33`, also listed in `data-model.md:12`) is never used. Step limits are hard-coded per call: discover 6, map 15, dropdown 4, fix 10.
- The "In flux (working tree, uncommitted)" section of `docs/wiki/architecture.md` and `docs/wiki/index.md:4` describe the revamp as uncommitted. It is committed (999f57a, 2911263).
- The `read_form_schema` tool description (`tools.py:380`) and the wiki say it reads "the form(s) on the current page". It reads the top document only, with no iframes and no shadow roots (F3).

## Cross-area notes
- **gmail-job-discovery-engineer:** 302/302 queued jobs are jobright.ai. 62% were already closed when fetched, and 2 are `/jobs/recommend` pages rather than postings, yet all were promoted. Admission should drop closed and listing URLs and call the F2 resolver, so that `canonical_url` is the employer's ATS URL. Today 303/336 `job_posting.canonical_url` values are empty, and `ats_type` holds the default "greenhouse" on all 336 rows.
- **application-forms-engineer:**
  - When nothing else matches, `FIND_SUBMIT_JS` picks "the only submit button in scope" (`submit.py:68`). Once F1 has accepted the wrong form, that means "Search" or "Create Account". Scope it to the classified form's container.
  - The JS submit guard is only installed around the fix agent (`fill.py:561-565`). The discover, map and dropdown agents run unguarded with the default `input`, `send_keys` and `click` tools. That is low risk while the form is still empty.
  - The shadow-DOM part of F3 changes the shared `[data-ja-field]` lookup contract.
- **browser-agent-qa-engineer:** This audit's synthetic pages and stubbed-model `discover()` harness (above) are a starting point for a discover-ladder suite. `save_fixture.py` can't capture iframe, shadow-DOM or JS-timing cases, so those need locally served synthetic pages.

## Proposed action plan
1. **Form-ready classifier and code-side account-wall detection, plus the stubbed-model discover-ladder test harness (F1, tests from F4).** Outcome: no non-application form gets through discover or map, and password/account pages raise `login_wall` with zero model calls.
2. **Aggregator resolution (F2).** jobright fixtures → a `JOBRIGHT` profile with a resolver and code-side closed detection, exposed for ingestion. Outcome: the discovery queue resolves to employer ATS URLs, and closed jobs never reach the model tiers.
3. **Generic iframe-embed following (F3, part 1).** Outcome: ATS forms embedded on company sites reach form-ready in code, and Greenhouse's special case becomes a fast path.
4. **Discover observability and failure classification (F4), then page-wide one-shot candidates and condition-based waits (F5).** Outcome: every discover exit is recorded and classified, the ladder never runs twice, and fewer runs fall back to the agent loop.
- **Dependencies / collaboration:**
  - application-forms-engineer: (1) and (3) need the submit locator scoped to the classified container.
  - gmail-job-discovery-engineer: (2) needs the resolver called at admission.
  - User: (2) needs the user to run `scripts/save_fixture.py` on 2–3 live jobright URLs.
  - temporal-platform-engineer: the retry change in (4).
  - browser-agent-qa-engineer: owns the shared discover suite.
- **Defer:**
  - A Workday board profile, agentic account creation (needs a product/consent decision; `generate_password` is unused) and multi-step wizard support (forms-led). Wait until (1) makes account walls explicit and (4) can measure them.
  - Shadow-DOM deep queries and a frame-aware `run_js`. Wait until (3) shows what is left.
  - Cutting the navigation agent's default tool surface (`search`, `navigate`, `input`) down to least privilege.
