"""
Greenhouse-specific form extraction, auth handling, and deterministic filling.

Extraction: reads the DOM to produce a FormSchema.
Auth: detects login/signup walls and creates an account with generated credentials.
Filling: takes FieldMappings and applies them via Playwright locators.
Select fields use an LLM fallback to pick the closest option when exact match fails.
"""
import re
from datetime import datetime

import httpx
from loguru import logger
from playwright.async_api import Frame, Page
from pydantic import BaseModel

from yahbah.llm.client import OllamaClient
from yahbah.schemas import FieldMapping, FormField, FormSchema


async def activate_and_resolve_embed(page: Page) -> Page | Frame:
    """
    For company career pages with embedded Greenhouse forms:
      1. Clicks the Application tab to trigger the embed script
      2. Waits for the Greenhouse iframe to appear
      3. Returns the iframe's Frame

    For direct Greenhouse pages (no #grnhse_app), returns the original Page.
    Both Page and Frame share the locator/query_selector API.
    """
    app_div = await page.query_selector("#grnhse_app")
    if not app_div:
        # No #grnhse_app wrapper. Find where the form fields actually live
        # by checking parent page and all frames. Greenhouse has multiple
        # embed patterns (iframe, DOM injection, etc.) — this handles all.
        best, best_count = page, 0

        # Check parent page for visible form fields
        parent_count = await page.locator(
            "input:not([type='hidden']):not([type='submit']):visible, "
            "select:visible, textarea:visible"
        ).count()
        if parent_count > 0:
            best, best_count = page, parent_count
            logger.debug(f"[embed] Parent page has {parent_count} visible form fields")

        # Check each frame
        for f in page.frames:
            if f == page.main_frame:
                continue
            try:
                count = await f.locator(
                    "input:not([type='hidden']):not([type='submit']):visible, "
                    "select:visible, textarea:visible"
                ).count()
                if count > best_count:
                    best, best_count = f, count
                    logger.debug(f"[embed] Frame {f.url[:60]} has {count} visible form fields")
            except Exception:
                continue

        if best != page:
            logger.info(
                f"[embed] Using iframe with {best_count} fields: {best.url[:80]}"
            )
        elif best_count > 0:
            logger.info(f"[embed] Form fields on parent page ({best_count} fields)")
        else:
            logger.warning("[embed] No visible form fields found anywhere")

        return best

    # Always click the Application tab to make the panel visible.
    # The Greenhouse embed script may create the iframe on page load,
    # but the tab panel has a `hidden` attribute that must be removed
    # by clicking the tab before the iframe content is interactable.
    logger.info("[embed] #grnhse_app detected — clicking Application tab")
    tab_selectors = [
        "#tab-application",
        ".js-job-app",
        "button[aria-controls*='application']",
        "button:has-text('Application')",
        ".apply-btn",
    ]
    for selector in tab_selectors:
        try:
            el = page.locator(selector).first
            if await el.count() > 0 and await el.is_visible():
                await el.click()
                logger.debug(f"[embed] Clicked tab: {selector}")
                break
        except Exception:
            continue

    # Wait for the iframe to appear (or be ready if it was pre-created)
    try:
        await page.wait_for_selector(
            "#grnhse_app iframe, #grnhse_iframe",
            state="attached",
            timeout=10_000,
        )
    except Exception:
        logger.warning("[embed] No iframe appeared — checking for direct DOM injection")
        has_fields = await page.query_selector(
            "#grnhse_app input:not([type='hidden']), "
            "#grnhse_app select, #grnhse_app textarea"
        )
        if has_fields:
            logger.info("[embed] Form fields found directly in DOM (no iframe)")
        return page

    iframe_el = await page.query_selector("#grnhse_app iframe, #grnhse_iframe")

    if not iframe_el:
        return page

    # Get the iframe's frame
    frame = await iframe_el.content_frame()
    if not frame:
        for f in page.frames:
            if "greenhouse" in (f.url or ""):
                frame = f
                break

    if not frame:
        logger.warning("[embed] Could not get iframe frame")
        return page

    logger.info(f"[embed] Resolved Greenhouse iframe: {frame.url}")

    # Wait for form fields inside the iframe
    try:
        await frame.wait_for_selector(
            "input:not([type='hidden']), select, textarea",
            state="visible",
            timeout=10_000,
        )
        logger.info("[embed] Iframe form fields are visible")
    except Exception:
        logger.warning("[embed] Timeout waiting for form fields inside iframe")

    return frame


# Confidence threshold below which we refuse to fill a required field
MIN_CONFIDENCE = 0.7


class _SelectPick(BaseModel):
    selected: int | None = None  # 1-based index into the option list


class _FieldNotFoundError(Exception):
    """Raised when no locator strategy can find a field on the page."""


class GreenhouseAuthHandler:
    """
    Detects Greenhouse auth walls and creates a new account.

    Greenhouse shows a sign-in/sign-up page when the job requires an account.
    We detect this by looking for password inputs or known auth form selectors,
    then fill the registration form with generated credentials.
    """

    # Selectors that indicate an auth wall is present
    _AUTH_INDICATORS = [
        "input[type='password']",
        "#new_user",
        "form[action*='sign_in']",
        "form[action*='users']",
        "[data-testid='sign-in-form']",
    ]

    # Selectors for the sign-up / create-account link
    _SIGNUP_LINK_SELECTORS = [
        "a[href*='sign_up']",
        "a[href*='register']",
        "a[href*='new_user']",
        "a:has-text('Create account')",
        "a:has-text('Sign up')",
    ]

    def __init__(self, page: Page) -> None:
        self._page = page

    async def needs_auth(self) -> bool:
        """Returns True if the current page has an auth wall."""
        for selector in self._AUTH_INDICATORS:
            el = await self._page.query_selector(selector)
            if el:
                return True
        return False

    async def create_account(self, email: str, password: str) -> None:
        """
        Creates a new account using the provided credentials.
        Raises RuntimeError if the form cannot be located or submitted.
        """
        # Try navigating to sign-up form if we're on the sign-in page
        for selector in self._SIGNUP_LINK_SELECTORS:
            link = await self._page.query_selector(selector)
            if link:
                await link.click()
                await self._page.wait_for_load_state("networkidle", timeout=10_000)
                logger.debug(f"Navigated to sign-up form via '{selector}'")
                break

        # Locate email input
        email_input = (
            await self._page.query_selector("input[type='email']")
            or await self._page.query_selector("input[name='user[email]']")
            or await self._page.query_selector("input[name='email']")
        )
        # Locate all password inputs (password + confirm password)
        password_inputs = await self._page.query_selector_all("input[type='password']")

        if not email_input:
            raise RuntimeError("Auth wall detected but no email input found")
        if not password_inputs:
            raise RuntimeError("Auth wall detected but no password input found")

        await email_input.fill(email)
        for pw_input in password_inputs:
            await pw_input.fill(password)

        # Submit the form
        submit = (
            await self._page.query_selector("input[type='submit']")
            or await self._page.query_selector("button[type='submit']")
        )
        if not submit:
            raise RuntimeError("Auth form submit button not found")

        async with self._page.expect_navigation(
            timeout=15_000, wait_until="domcontentloaded"
        ):
            await submit.click()

        logger.info(f"Account created for {email}")


class GreenhouseExtractor:
    """
    Extracts all interactive form fields from a Greenhouse application page.

    Greenhouse forms live inside <div id="application_form"> (or similar).
    We locate all input, select, and textarea elements, resolve their labels,
    and return a FormSchema.
    """

    def __init__(self, page: Page) -> None:
        self._page = page
        self._root_page = page  # kept for job description / metadata on embedded pages

    async def validate_application_page(self) -> None:
        """
        Raises ValueError if the current page is not a job application form.
        Catches the common case of a taken-down Greenhouse job that redirects
        to the company's job board (URL contains ?error=true, or the path no
        longer has a numeric job ID).

        Accepts:
          - Direct Greenhouse URLs: /jobs/<numeric-id>
          - Embedded Greenhouse forms: pages with #grnhse_app or a Greenhouse
            embed script (e.g. Airbnb's /positions/<id> tab UI)
        """
        url = self._page.url
        if "error=true" in url:
            raise ValueError(f"Job posting no longer available (redirected to: {url})")

        # Direct Greenhouse URL
        if re.search(r"/jobs/\d+", url):
            return

        # Embedded Greenhouse form (e.g. company career sites with tab UI)
        has_embed = await self._page.evaluate("""() => {
            if (document.querySelector('#grnhse_app')) return true;
            if (document.querySelector('script[src*="boards.greenhouse.io"]')) return true;
            if (document.querySelector('iframe[src*="greenhouse"]')) return true;
            return false;
        }""")
        if has_embed:
            logger.info(f"[validate] Embedded Greenhouse form detected on {url}")
            return

        title = await self._page.title()
        raise ValueError(
            f"Page does not appear to be a job application form "
            f"(url={url!r}, title={title!r})"
        )

    async def _activate_embedded_form(self) -> None:
        """
        Delegates to the standalone activate_and_resolve_embed() and switches
        self._page to the iframe frame if applicable.
        """
        resolved = await activate_and_resolve_embed(self._page)
        if resolved is not self._page:
            self._page = resolved  # type: ignore[assignment]

    async def extract(self) -> tuple[FormSchema, str]:
        """
        Returns (FormSchema, job_description_text).
        job_description is scraped from the page for the cover letter prompt.
        """
        # Wait for the page to be usable. "load" is sufficient — "networkidle"
        # is unreliable on pages with background polling/analytics (e.g. Reddit).
        logger.debug(f"[extract] Waiting for page load — URL: {self._page.url}")
        await self._page.wait_for_load_state("load", timeout=30_000)
        logger.debug("[extract] 'load' state reached")
        try:
            await self._page.wait_for_load_state("networkidle", timeout=5_000)
            logger.debug("[extract] 'networkidle' state reached")
        except Exception:
            logger.debug("[extract] 'networkidle' timed out — proceeding anyway")
        await self.validate_application_page()
        logger.debug(f"[extract] Page validated — title: {await self._page.title()!r}")

        # Extract job description from the main page first. On some embedded
        # pages the description is on the Role Overview tab (parent page), but
        # on others everything lives inside the Greenhouse iframe.
        job_description = await self._extract_job_description()

        await self._activate_embedded_form()

        # If the parent page text was too short (e.g. just a tagline), try
        # extracting from the iframe/activated page instead.
        if len(job_description) < 500:
            iframe_description = await self._extract_job_description()
            if len(iframe_description) > len(job_description):
                logger.debug(
                    f"[extract] Parent page text too short ({len(job_description)} chars), "
                    f"using iframe text ({len(iframe_description)} chars)"
                )
                job_description = iframe_description

        fields: list[FormField] = []

        # ── Inputs ────────────────────────────────────────────────────────────
        input_handles = await self._page.query_selector_all(
            "input:not([type='hidden']):not([type='submit'])"
        )
        for handle in input_handles:
            field = await self._extract_input(handle)
            if field:
                fields.append(field)

        # ── Selects ────────────────────────────────────────────────────────────
        select_handles = await self._page.query_selector_all("select")
        for handle in select_handles:
            field = await self._extract_select(handle)
            if field:
                fields.append(field)

        # ── Textareas ─────────────────────────────────────────────────────────
        textarea_handles = await self._page.query_selector_all("textarea")
        for handle in textarea_handles:
            field = await self._extract_textarea(handle)
            if field:
                fields.append(field)

        # Deduplicate fields with identical labels — Greenhouse sometimes
        # renders accessibility/shadow duplicates that are both visible.
        seen_labels: set[str] = set()
        unique_fields: list[FormField] = []
        for field in fields:
            key = field.label.strip().lower()
            if key not in seen_labels:
                seen_labels.add(key)
                unique_fields.append(field)
            else:
                logger.debug(f"[extract] Skipping duplicate field: {field.label!r}")
        fields = unique_fields

        logger.info(f"Extracted {len(fields)} form fields")

        page_url = self._page.url
        page_title = await self._page.title()

        return FormSchema(fields=fields, page_url=page_url, page_title=page_title), job_description

    async def _label_for(self, handle) -> str:
        """Resolves label text for a form element."""
        el_id = await handle.get_attribute("id")
        if el_id:
            label = await self._page.query_selector(f"label[for='{el_id}']")
            if label:
                text = await label.inner_text()
                return text.strip().rstrip("*").strip()

        # Fallback: nearest ancestor label
        text = await handle.evaluate(
            """el => {
                let node = el.parentElement;
                while (node) {
                    if (node.tagName === 'LABEL') return node.innerText;
                    const label = node.querySelector('label');
                    if (label) return label.innerText;
                    node = node.parentElement;
                }
                return '';
            }"""
        )
        return (text or "").strip().rstrip("*").strip()

    async def _is_required(self, handle) -> bool:
        required = await handle.get_attribute("required")
        aria_required = await handle.get_attribute("aria-required")
        return required is not None or aria_required == "true"

    async def _extract_input(self, handle) -> FormField | None:
        field_type = (await handle.get_attribute("type") or "text").lower()
        if field_type in ("button", "reset", "image"):
            return None

        # Skip invisible non-file/non-checkbox inputs — they are hidden
        # shadow/duplicate elements (e.g. custom React dropdown fields).
        if field_type not in ("file", "checkbox") and not await handle.is_visible():
            return None

        # Skip invisible checkboxes — only extract ones the user can actually see.
        if field_type == "checkbox" and not await handle.is_visible():
            return None

        # Skip the intl-tel-input country-code *search* widget inside the
        # dropdown — but keep the actual phone number input (type="tel").
        if await handle.evaluate(
            "el => !!el.closest('.iti') && el.type !== 'tel'"
        ):
            return None

        label = await self._label_for(handle)
        if not label:
            label = await handle.get_attribute("placeholder") or ""
        if not label:
            label = await handle.get_attribute("name") or ""
        if not label:
            return None  # can't identify it

        return FormField(
            label=label,
            field_type=field_type,
            name=await handle.get_attribute("name"),
            element_id=await handle.get_attribute("id"),
            placeholder=await handle.get_attribute("placeholder"),
            required=await self._is_required(handle),
        )

    async def _extract_select(self, handle) -> FormField | None:
        label = await self._label_for(handle)
        if not label:
            label = await handle.get_attribute("name") or ""
        if not label:
            return None

        # Collect option texts
        option_texts: list[str] = await handle.evaluate(
            "sel => Array.from(sel.options).map(o => o.text).filter(t => t.trim())"
        )

        return FormField(
            label=label,
            field_type="select",
            name=await handle.get_attribute("name"),
            element_id=await handle.get_attribute("id"),
            required=await self._is_required(handle),
            options=option_texts,
        )

    async def _extract_textarea(self, handle) -> FormField | None:
        # Skip reCAPTCHA hidden textarea
        name = await handle.get_attribute("name") or ""
        if "g-recaptcha" in name:
            return None
        # Skip invisible textareas
        if not await handle.is_visible():
            return None

        label = await self._label_for(handle)
        if not label:
            label = await handle.get_attribute("placeholder") or ""
        if not label:
            label = await handle.get_attribute("name") or ""
        if not label:
            return None

        return FormField(
            label=label,
            field_type="textarea",
            name=await handle.get_attribute("name"),
            element_id=await handle.get_attribute("id"),
            placeholder=await handle.get_attribute("placeholder"),
            required=await self._is_required(handle),
        )

    async def _extract_job_description(self) -> str:
        """Best-effort scrape of job description text."""
        selectors = [
            "#content",
            ".job-description",
            "[data-testid='job-description']",
            "article",
            "main",
        ]
        for sel in selectors:
            el = await self._page.query_selector(sel)
            if el:
                text = await el.inner_text()
                if len(text) > 100:
                    return text
        return await self._page.evaluate("document.body.innerText") or ""

    async def extract_job_metadata(self) -> tuple[str | None, str | None, str | None, str]:
        """
        Scrapes structured job metadata from the page.
        Returns (title, company, location, canonical_url).
        canonical_url is page.url after all redirects.

        Uses _root_page (the main page, not an iframe) so metadata is found
        on embedded career pages where title/company/location live outside
        the Greenhouse iframe.
        """
        page = self._root_page
        canonical_url = page.url

        async def _first_text(*selectors: str) -> str | None:
            for sel in selectors:
                try:
                    el = page.locator(sel).first
                    await el.wait_for(state="attached", timeout=1_000)
                    text = (await el.text_content() or "").strip()
                    if text:
                        return text
                except Exception:
                    continue
            return None

        title = await _first_text(
            "h1.app-title", ".app-body h1", "h1",
            ".posting-headline h2", "[data-qa='posting-name']",
        )
        company = await _first_text(
            ".company-name", "[data-qa='company-name']",
            ".company", "[data-company]",
        )
        location = await _first_text(
            ".location", ".app-body .location", ".posting-categories .location",
            "[data-qa='job-location']", ".job-location",
        )

        # Fall back to parsing the <title> tag
        if not title or not company:
            page_title = await page.title()
            m = re.search(
                r"Apply(?:\s+for)?\s+(.+?)\s+at\s+(.+?)(?:\s*[|–—\-]|$)",
                page_title,
                re.IGNORECASE,
            )
            if m:
                title = title or m.group(1).strip()
                company = company or m.group(2).strip()

        return title, company, location, canonical_url

    async def extract_posted_date(self) -> str | None:
        """Extract the job posting date from structured data on the page.

        Checks (in priority order):
        1. JSON-LD JobPosting schema (datePosted field)
        2. HTML meta tags (article:published_time, date, etc.)
        3. <time> elements with datetime attribute
        """
        page = self._root_page

        date = await page.evaluate("""() => {
            // 1. JSON-LD structured data
            const ldScripts = document.querySelectorAll('script[type="application/ld+json"]');
            for (const script of ldScripts) {
                try {
                    let data = JSON.parse(script.textContent);
                    // Handle @graph arrays
                    if (data['@graph']) data = data['@graph'];
                    const items = Array.isArray(data) ? data : [data];
                    for (const item of items) {
                        if (item.datePosted) return item.datePosted;
                        if (item['@type'] === 'JobPosting' && item.datePosted) return item.datePosted;
                    }
                } catch {}
            }

            // 2. Meta tags
            const metaSelectors = [
                'meta[property="article:published_time"]',
                'meta[name="date"]',
                'meta[name="publish-date"]',
                'meta[name="DC.date"]',
                'meta[property="og:article:published_time"]',
            ];
            for (const sel of metaSelectors) {
                const el = document.querySelector(sel);
                if (el) {
                    const content = el.getAttribute('content');
                    if (content) return content;
                }
            }

            // 3. <time> elements
            const timeEl = document.querySelector('time[datetime]');
            if (timeEl) return timeEl.getAttribute('datetime');

            return null;
        }""")

        if not date:
            # 4. Greenhouse public API fallback
            url = self._root_page.url
            # Try multiple URL patterns to find slug + job_id
            slug, job_id = None, None

            # Pattern 1: boards.greenhouse.io/{slug}/jobs/{id}
            m = re.search(r"greenhouse\.io/(\w+)/jobs/(\d+)", url)
            if m:
                slug, job_id = m.group(1), m.group(2)

            # Pattern 2: embedded ?for={slug}&token={id}
            if not slug:
                m = re.search(r"for=(\w+).*?token=(\d+)", url)
                if m:
                    slug, job_id = m.group(1), m.group(2)

            # Pattern 3: gh_jid= param on parent URL, slug from iframe
            if not slug:
                m = re.search(r"gh_jid=(\d+)", url)
                if m:
                    job_id = m.group(1)
                    # Get slug from greenhouse iframe URL
                    for f in self._root_page.frames:
                        fm = re.search(r"for=(\w+)", f.url or "")
                        if fm:
                            slug = fm.group(1)
                            break

            if slug and job_id:
                try:
                    async with httpx.AsyncClient(timeout=5.0) as client:
                        resp = await client.get(
                            f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{job_id}"
                        )
                        if resp.status_code == 200:
                            data = resp.json()
                            date = data.get("updated_at")
                            logger.debug(f"[posted_date] Greenhouse API: {date}")
                except Exception:
                    pass

        if not date:
            return None

        # Parse and normalize to YYYY-MM-DD
        date = date.strip()
        for fmt in (
            "%Y-%m-%dT%H:%M:%S%z",      # 2026-04-03T14:32:21-04:00
            "%Y-%m-%dT%H:%M:%S.%f%z",    # with microseconds
            "%Y-%m-%dT%H:%M:%S",          # no timezone
            "%Y-%m-%d",                    # already date-only
            "%B %d, %Y",                   # April 3, 2026
            "%b %d, %Y",                   # Apr 3, 2026
            "%m/%d/%Y",                    # 04/03/2026
            "%d/%m/%Y",                    # 03/04/2026
        ):
            try:
                return datetime.strptime(date, fmt).strftime("%Y-%m-%d")
            except ValueError:
                continue

        # Last resort: dateutil if available
        try:
            from dateutil.parser import parse as dateutil_parse
            return dateutil_parse(date).strftime("%Y-%m-%d")
        except Exception:
            logger.debug(f"[posted_date] Could not parse date: {date!r}")
            return None


class GreenhouseFiller:
    """
    Fills a Greenhouse form deterministically from FieldMappings.

    Locator strategy (tried in order, each with a short 3 s timeout):
      1. #element_id  (most precise — from extracted FormField)
      2. [name="x"]   (HTML name attribute)
      3. get_by_label (fuzzy)
      4. get_by_placeholder (fuzzy)

    Non-required fields that can't be located are skipped with a warning.
    Required fields that can't be located raise immediately.
    """

    _LOCATE_TIMEOUT = 7_000   # ms per locator attempt
    _UPLOAD_TIMEOUT = 60_000  # ms — file inputs may be lazily rendered

    def __init__(
        self,
        page: Page | Frame,
        form_schema: FormSchema | None = None,
        llm_client: OllamaClient | None = None,
    ) -> None:
        self._page = page
        self._llm = llm_client or OllamaClient()
        # label → FormField lookup for precise targeting
        self._field_by_label: dict[str, FormField] = (
            {f.label: f for f in form_schema.fields} if form_schema else {}
        )

    @property
    def _keyboard(self):
        """Keyboard access that works for both Page and Frame (iframe)."""
        if hasattr(self._page, "keyboard"):
            return self._page.keyboard
        return self._page.page.keyboard

    async def fill(
        self,
        field_mappings: list[FieldMapping],
        cover_letter_path: str | None,
        cover_letter_text: str | None = None,
    ) -> list[dict]:
        # Ensure React/JS has fully hydrated before touching any field.
        # Without this, the first field(s) may be filled before event listeners
        # are attached, causing silent no-ops (especially on a fresh page load).
        logger.debug(f"[fill] Waiting for page load — URL: {self._page.url}")
        await self._page.wait_for_load_state("load", timeout=30_000)
        try:
            await self._page.wait_for_load_state("networkidle", timeout=5_000)
        except Exception:
            logger.debug("[fill] 'networkidle' timed out — proceeding anyway")

        submitted_values: list[dict] = []
        seen_labels: set[str] = set()
        for mapping in field_mappings:
            # File uploads use well-known IDs, not labels — exempt from dedup
            if mapping.mapped_to not in ("resume", "cover_letter") and mapping.form_label in seen_labels:
                logger.debug(f"[fill] Skipping duplicate mapping for {mapping.form_label!r}")
                continue
            seen_labels.add(mapping.form_label)

            if mapping.confidence < MIN_CONFIDENCE:
                form_field_check = self._field_by_label.get(mapping.form_label)
                is_required = form_field_check and form_field_check.required
                if mapping.mapped_to not in ("cover_letter", "resume") and not is_required:
                    logger.warning(
                        f"Skipping optional '{mapping.form_label}' — confidence {mapping.confidence:.2f} below threshold"
                    )
                    continue
                if is_required:
                    logger.warning(
                        f"Low confidence ({mapping.confidence:.2f}) for required field "
                        f"'{mapping.form_label}' — attempting fill anyway"
                    )

            form_field = self._field_by_label.get(mapping.form_label)
            logger.debug(
                f"[fill] Filling {mapping.form_label!r} "
                f"(mapped_to={mapping.mapped_to!r}, type={form_field.field_type if form_field else '?'}, "
                f"required={form_field.required if form_field else '?'})"
            )
            try:
                submitted = await self._fill_field(mapping, form_field, cover_letter_path, cover_letter_text)
            except _FieldNotFoundError:
                if form_field and form_field.required:
                    raise RuntimeError(
                        f"Could not locate required field '{mapping.form_label}'"
                    )
                logger.warning(f"Could not locate optional field '{mapping.form_label}' — skipping")
                continue
            except Exception as exc:
                logger.error(
                    f"[fill] FAILED on field {mapping.form_label!r} "
                    f"(mapped_to={mapping.mapped_to!r}, value={mapping.value[:60]!r}): {exc}"
                )
                raise

            submitted_values.append({
                "form_label": mapping.form_label,
                "mapped_to": mapping.mapped_to,
                "value": submitted if submitted is not None else "(file upload)",
            })

            # Best-effort settle wait — lets React/Vue controlled inputs re-render.
            # Timeout is intentionally swallowed; missing it just risks a stale screenshot.
            try:
                await self._page.wait_for_load_state("domcontentloaded", timeout=2_000)
            except Exception:
                pass

        # ── Fill additional education entries ─────────────────────────────────────
        # If the profile has multiple education entries and the form has an
        # "Add another" button in the education section, click it and fill
        # subsequent entries directly by their indexed IDs.
        await self._fill_additional_education(submitted_values)

        # ── Auto-check any dynamically-revealed required checkboxes ─────────────
        # Greenhouse injects consent checkboxes (e.g. GDPR demographic consent)
        # into the DOM only after certain sections are filled. They are never in
        # the extracted schema, so we scan for them here and check them.
        await self._page.wait_for_timeout(500)  # brief settle for dynamic injection
        checkbox_handles = await self._page.query_selector_all(
            "input[type='checkbox'][required]:not([aria-hidden='true']), "
            "input[type='checkbox'][aria-required='true']:not([aria-hidden='true'])"
        )
        for handle in checkbox_handles:
            try:
                if not await handle.is_visible():
                    continue
                if await handle.is_checked():
                    continue
                el_id = await handle.get_attribute("id") or ""
                name = await handle.get_attribute("name") or el_id
                await handle.check()
                logger.info(f"Auto-checked required checkbox: {name!r}")
            except Exception as exc:
                logger.warning(f"Could not auto-check checkbox: {exc}")

        # Scroll to bottom so all filled values are rendered before caller screenshots
        await self._page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await self._page.wait_for_load_state("networkidle", timeout=5_000)

        return submitted_values

    async def _fill_field(
        self,
        mapping: FieldMapping,
        form_field: FormField | None,
        cover_letter_path: str | None,
        cover_letter_text: str | None = None,
    ) -> str | None:
        """Fill a single field. Returns the value that was actually submitted, or None for file uploads."""
        value = mapping.value
        mapped_to = mapping.mapped_to

        # ── File uploads ──────────────────────────────────────────────────────
        # Greenhouse uses a custom React file upload component. Directly calling
        # set_input_files() on the hidden input bypasses the component's click
        # lifecycle and crashes it. The correct approach is expect_file_chooser():
        # click-trigger the input so the component opens the OS dialog, then
        # intercept that dialog and inject the file — satisfying the component's
        # expected flow. set_input_files() is kept as a fallback for plain inputs.
        if mapped_to == "resume":
            # Target by well-known Greenhouse ID first — the label "Attach" is
            # shared across resume and cover-letter inputs so form_field lookup
            # is unreliable.  Fall back to form_field metadata, then first file input.
            candidates = [
                self._page.locator("#resume"),
                self._page.locator("input[type='file'][id*='resume' i]"),
            ]
            if form_field and form_field.element_id and form_field.element_id != "resume":
                candidates.append(self._page.locator(f"#{form_field.element_id}"))
            if form_field and form_field.name:
                candidates.append(self._page.locator(f"[name='{form_field.name}']"))
            candidates.append(self._page.locator("input[type='file']").first)

            for loc in candidates:
                try:
                    await loc.wait_for(state="attached", timeout=self._LOCATE_TIMEOUT)
                    await self._upload_file(loc, value)
                    logger.debug(f"Uploaded resume: {value}")
                    return None
                except Exception:
                    continue
            raise _FieldNotFoundError("resume file input")

        if mapped_to == "cover_letter":
            # Determine whether the cover letter field is a file upload or text entry.
            # Greenhouse pages may have either (or both — "Attach" + "Enter manually").
            is_file_field = form_field is None or form_field.field_type == "file"

            if is_file_field:
                if not cover_letter_path:
                    logger.warning("Cover letter field is a file upload but no PDF available — skipping")
                    return None
                # Target by well-known ID first, then form_field metadata, then
                # positional fallback (second file input).
                candidates = [
                    self._page.locator("#cover_letter"),
                    self._page.locator("input[type='file'][id*='cover' i]"),
                ]
                if form_field and form_field.element_id and form_field.element_id != "cover_letter":
                    candidates.append(self._page.locator(f"#{form_field.element_id}"))
                if form_field and form_field.name:
                    candidates.append(self._page.locator(f"[name='{form_field.name}']"))
                candidates.append(self._page.locator("input[type='file']").nth(1))

                for loc in candidates:
                    try:
                        await loc.wait_for(state="attached", timeout=self._LOCATE_TIMEOUT)
                        await self._upload_file(loc, cover_letter_path)
                        logger.debug(f"Uploaded cover letter PDF: {cover_letter_path}")
                        return None
                    except Exception:
                        continue

                raise _FieldNotFoundError("cover letter file input")

            else:
                # Text/textarea field — paste the cover letter body directly
                if not cover_letter_text:
                    logger.warning("Cover letter field is text entry but no text available — skipping")
                    return None
                locator = await self._resolve_locator(mapping.form_label, form_field)
                await locator.fill(cover_letter_text, timeout=self._LOCATE_TIMEOUT)
                logger.debug(f"Filled cover letter text into '{mapping.form_label}'")
                return "(cover letter text)"

        # ── Checkboxes ────────────────────────────────────────────────────────
        if form_field and form_field.field_type == "checkbox":
            locator = await self._resolve_locator(mapping.form_label, form_field)
            await locator.check(timeout=self._LOCATE_TIMEOUT)
            logger.debug(f"Checked checkbox '{mapping.form_label}'")
            return "true"

        # ── Build locator chain ───────────────────────────────────────────────
        locator = await self._resolve_locator(mapping.form_label, form_field)

        # Determine whether this field needs dropdown-selection logic.
        # Priority order:
        #   1. form_field.field_type == "select" — native <select> from extractor
        #   2. Live DOM check: tag is "select", or element/ancestor has
        #      role="combobox" (custom React Select / Greenhouse EEO dropdowns)
        if form_field and form_field.field_type == "select":
            is_select = True
        else:
            try:
                dom_kind = await locator.evaluate(
                    """el => {
                        if (el.tagName.toLowerCase() === 'select') return 'select';
                        // Check element itself for combobox signals
                        if (el.getAttribute('role') === 'combobox'
                            || el.getAttribute('aria-haspopup')
                            || el.getAttribute('aria-autocomplete')
                            || el.getAttribute('aria-expanded') !== null) return 'combobox';
                        // Walk UP ancestors
                        let node = el.parentElement;
                        while (node) {
                            if (node.getAttribute('role') === 'combobox'
                                || node.getAttribute('aria-haspopup')
                                || node.classList?.contains('select__control')
                                || node.classList?.contains('select__container')) return 'combobox';
                            node = node.parentElement;
                        }
                        // Walk DOWN: the resolved element may be a wrapper around
                        // the actual combobox input (e.g. a label target div).
                        if (el.querySelector('[role="combobox"], [role="listbox"], [aria-autocomplete]'))
                            return 'combobox';
                        return 'input';
                    }""",
                    timeout=self._LOCATE_TIMEOUT,
                )
                is_select = dom_kind in ("select", "combobox")
            except Exception:
                is_select = False

        if is_select:
            required = bool(form_field and form_field.required)
            await self._select_best_option(locator, mapping.form_label, value, required=required)
            return value
        else:

            # TODO FIX- this assumes country phone codes always start with +
            # For phone inputs inside intl-tel-input, strip the country code
            # prefix — the widget manages the country code via its own dropdown.
            if form_field and form_field.field_type == "tel":
                value = re.sub(r"^\+\d{1,3}[-.\s]?", "", value)
            await locator.fill(value, timeout=self._LOCATE_TIMEOUT)
            printable = value if len(value) <= 40 else f"{value[:40]}–"
            logger.debug(f"Filled '{mapping.form_label}' → '{printable}'")
            return value

    async def _select_best_option(self, locator, field_label: str, value: str, required: bool = False) -> None:
        """
        Selects the best matching option for both native <select> and custom
        React dropdown components.

        Native <select>:
          1. Exact label / value-attribute match (no LLM, fastest)
          2. Read live option list → LLM picks → select by exact string

        Custom dropdown (tag != "select"):
          1. Click trigger to open the dropdown
          2. Collect visible option elements (text → DOM index, one pass)
          3. LLM picks best match → click that option by index
        """
        # CSS selector covering the most common custom-dropdown option patterns
        _CUSTOM_OPT = (
            "[role='option'], [role='listbox'] li, "
            "[role='menu'] [role='menuitem'], "
            ".select__option, .dropdown-item"
        )

        tag = await locator.evaluate(
            "el => el.tagName.toLowerCase()", timeout=self._LOCATE_TIMEOUT
        )

        if tag == "select":
            # ── Native <select> ───────────────────────────────────────────────
            for kwargs in ({"label": value}, {"value": value}):
                try:
                    await locator.select_option(**kwargs, timeout=self._LOCATE_TIMEOUT)
                    logger.debug(f"Selected '{value}' for '{field_label}' (exact)")
                    return
                except Exception:
                    pass

            raw: list[dict] = await locator.evaluate(
                "el => Array.from(el.options).map(o => ({text: o.text.trim(), value: o.value}))",
                timeout=self._LOCATE_TIMEOUT,
            )
            option_texts = [o["text"] for o in raw if o["text"]]

        else:
            # ── Custom dropdown / autocomplete ────────────────────────────────
            opt_loc = self._page.locator(_CUSTOM_OPT)

            # Resolve the clickable/typeable element: React Select wraps the
            # real <input> inside a container div.  Clicking/typing on the
            # wrapper often does nothing — we need the inner <input>.
            inner_input = locator.locator("input").first
            try:
                await inner_input.wait_for(state="attached", timeout=1000)
                click_target = inner_input
            except Exception:
                click_target = locator

            # Single JS call to count visible options — much faster than
            # per-element is_visible() roundtrips, especially when hidden
            # option lists (e.g. phone country picker) inflate the DOM.
            _VIS_COUNT_JS = """() => {
                const sel = "[role='option'], [role='listbox'] li, " +
                            "[role='menu'] [role='menuitem'], " +
                            ".select__option, .dropdown-item";
                let vis = 0;
                for (const el of document.querySelectorAll(sel)) {
                    if (el.offsetParent !== null && el.offsetWidth > 0) vis++;
                    if (vis > 0) return vis;  // early-exit: we just need > 0
                }
                return 0;
            }"""

            async def _poll_options(seconds: float = 3.0) -> int:
                """Poll for visible dropdown options. Returns count when > 0."""
                iterations = int(seconds / 0.2)
                for _ in range(iterations):
                    await self._page.wait_for_timeout(200)
                    vis = await self._page.evaluate(_VIS_COUNT_JS)
                    if vis > 0:
                        return vis
                return 0

            # 1. Click to open/focus — wait up to 3 s for options to render.
            await click_target.click(timeout=self._LOCATE_TIMEOUT)
            visible_count = await _poll_options(3.0)

            # 2. Retry click ONLY if no options appeared.  Check once more
            #    before clicking — the first poll may have ended right before
            #    options rendered, and a second click would CLOSE the dropdown.
            if visible_count == 0:
                # One more instant check to avoid toggling an open dropdown
                visible_count = await self._page.evaluate(_VIS_COUNT_JS)
            if visible_count == 0:
                logger.debug(f"No options after first click for '{field_label}' — retrying click")
                await click_target.click(timeout=self._LOCATE_TIMEOUT)
                visible_count = await _poll_options(3.0)

            # 3. Last resort for true autocomplete fields (e.g. Location) where
            #    options only appear after typing.  Only reached when both clicks
            #    failed to reveal any options.
            #    Use press_sequentially() (real keystrokes) instead of fill()
            #    because React Select listens for native input events, not
            #    programmatic value changes.
            if visible_count == 0:
                search_term = value.split(",")[0].strip()
                logger.debug(f"No options after clicks — typing '{search_term}' for autocomplete")
                await click_target.click(timeout=self._LOCATE_TIMEOUT)
                await click_target.press_sequentially(search_term, delay=50)
                visible_count = await _poll_options(5.0)

            # Collect VISIBLE options in one JS call — avoids hundreds of
            # async roundtrips when hidden option lists inflate the DOM.
            _COLLECT_JS = """() => {
                const sel = "[role='option'], [role='listbox'] li, " +
                            "[role='menu'] [role='menuitem'], " +
                            ".select__option, .dropdown-item";
                const all = document.querySelectorAll(sel);
                const results = [];
                for (let i = 0; i < all.length; i++) {
                    const el = all[i];
                    if (el.offsetParent !== null && el.offsetWidth > 0) {
                        results.push({text: el.innerText.trim(), index: i});
                    }
                }
                return results;
            }"""
            raw_options = await self._page.evaluate(_COLLECT_JS)
            option_map: dict[str, int] = {}
            for entry in raw_options:
                t = entry["text"]
                if t and t not in option_map:
                    option_map[t] = entry["index"]

            if not option_map:
                await self._keyboard.press("Escape")
                if required:
                    raise RuntimeError(
                        f"No dropdown options found for required field:\n\t'{field_label}'"
                    )
                logger.warning(f"No dropdown options found after clicking:\n\t'{field_label}'")
                return

            option_texts = list(option_map.keys())

        if not option_texts:
            if required:
                raise RuntimeError(
                    f"No options found for required field '{field_label}'"
                )
            logger.warning(f"No options found for '{field_label}'")
            return

        # ── LLM picks best option by number ──────────────────────────────────
        # LLM returns a 1-based index — avoids hallucinated / mismatched text.
        numbered = "\n".join(f"  {i + 1}. {t}" for i, t in enumerate(option_texts))
        result = await self._llm.generate_structured(
            system_prompt=(
                "You are selecting the best matching option from a dropdown. "
                "Respond with the NUMBER of the best option (1-based). "
                "Choose the closest semantic match even if wording differs "
                '(e.g. "Male" → "Man", "Decline" → "I don\'t wish to answer"). '
                'Respond with valid JSON: {"selected": <number>}'
            ),
            user_prompt=(
                f'Field: "{field_label}"\n'
                f'Intended answer: "{value}"\n'
                f"Options:\n{numbered}"
            ),
            response_model=_SelectPick,
        )

        idx = result.selected
        if idx is None or not (1 <= idx <= len(option_texts)):
            msg = f"LLM returned invalid index {idx!r} for '{field_label}'"
            if required:
                if tag != "select":
                    await self._keyboard.press("Escape")
                raise RuntimeError(f"{msg} (field is required)")
            logger.warning(f"{msg} — skipping")
            if tag != "select":
                await self._keyboard.press("Escape")
            return

        selected = option_texts[idx - 1]  # convert 1-based → 0-based

        if tag == "select":
            await locator.select_option(label=selected, timeout=self._LOCATE_TIMEOUT)
        else:
            await opt_loc.nth(option_map[selected]).click(timeout=self._LOCATE_TIMEOUT)
            # Close the dropdown — React Select multi-selects stay open after a
            # click, causing the next field to inherit leftover visible options.
            await self._keyboard.press("Escape")
            await self._page.wait_for_timeout(150)

        logger.debug(f"Selected '{selected}' for '{field_label}'")

    async def _upload_file(self, locator, file_path: str) -> None:
        """
        Uploads a file via the OS file-chooser interception pattern.

        Custom upload components (e.g. Greenhouse's React uploader) crash when
        set_input_files() is called directly because they expect their own click
        handler to run first. expect_file_chooser() lets us click-trigger the
        component normally, intercept the dialog the browser would show, and
        inject the file — all without the OS dialog appearing.

        Falls back to set_input_files() for plain <input type="file"> elements
        (e.g. visually-hidden inputs that accept files without a chooser dialog).
        """
        # Brief wait for the element to appear in the DOM.
        logger.debug(f"[upload] Waiting for file input to attach (selector: {locator})")
        try:
            await locator.wait_for(state="attached", timeout=8_000)
            logger.debug("[upload] File input attached")
        except Exception:
            logger.debug("[upload] File input not yet attached — proceeding anyway")

        # Greenhouse file inputs are typically visually-hidden (clip/1px), so
        # JS-clicking the input itself or calling set_input_files() directly both
        # fail (browser blocks chooser on non-visible elements).
        # The reliable trigger is the associated <label for="id"> or the nearest
        # visible Attach button — we find it via JS and intercept the chooser.
        logger.debug("[upload] Attempting file chooser interception via label/button click")
        try:
            async with self._page.expect_file_chooser(
                timeout=self._LOCATE_TIMEOUT
            ) as fc_info:
                await locator.evaluate("""node => {
                    // 1. Associated <label for="id"> — most reliable for Greenhouse
                    if (node.id) {
                        const lbl = document.querySelector('label[for="' + node.id + '"]');
                        if (lbl) { lbl.click(); return; }
                    }
                    // 2. Nearest ancestor button (e.g. Attach btn wrapping the input)
                    let p = node.parentElement;
                    while (p && p !== document.body) {
                        const btn = p.querySelector('button:not([type="submit"]):not([aria-hidden])');
                        if (btn) { btn.click(); return; }
                        p = p.parentElement;
                    }
                    // 3. Direct click as last resort
                    node.click();
                }""")
            chooser = await fc_info.value
            await chooser.set_files(file_path)
            logger.debug(f"[upload] File chooser succeeded: {file_path}")
            return
        except Exception as exc:
            logger.debug(f"[upload] File chooser failed ({exc}) — falling back to set_input_files")

        # Final fallback: set_input_files() for plain visible file inputs that
        # don't go through the React chooser flow.
        logger.debug("[upload] Trying set_input_files directly")
        await locator.set_input_files(file_path, timeout=self._UPLOAD_TIMEOUT)
        logger.debug(f"[upload] set_input_files succeeded: {file_path}")

    async def _fill_additional_education(self, submitted_values: list[dict]) -> None:
        """Fill additional education entries beyond the first one.

        Greenhouse education sections use indexed field IDs (school--0, school--1, etc.)
        and an "Add another" button to create new entry rows. We fill index 0 via the
        normal field mapper, then directly fill subsequent entries here.
        """
        from yahbah.config import load_prompts_config

        education = load_prompts_config().get("profile", {}).get("education", [])
        if len(education) < 2:
            return

        # Check if there's an "Add another" button in an education section
        add_btn = self._page.locator(
            ".education--container .add-another-button, "
            "button.add-another-button"
        ).first
        try:
            if await add_btn.count() == 0 or not await add_btn.is_visible():
                return
        except Exception:
            return

        edu_field_keys = [
            ("school", "school--{idx}"),
            ("degree_type", "degree--{idx}"),
            ("discipline", "discipline--{idx}"),
            ("start_month", "start-month--{idx}"),
            ("start_year", "start-year--{idx}"),
            ("end_month", "end-month--{idx}"),
            ("end_year", "end-year--{idx}"),
        ]

        for i, edu in enumerate(education[1:], start=1):
            # Click "Add another" to create the new entry fields
            try:
                await add_btn.click()
                logger.info(f"[education] Clicked 'Add another' for entry {i}")
            except Exception as exc:
                logger.warning(f"[education] Failed to click 'Add another': {exc}")
                break

            # Wait for the new fields to appear
            new_field_id = f"school--{i}"
            try:
                await self._page.wait_for_selector(
                    f"#{new_field_id}, [id='{new_field_id}']",
                    state="visible",
                    timeout=5_000,
                )
            except Exception:
                logger.warning(
                    f"[education] Fields for entry {i} didn't appear after 5s — "
                    f"falling back to most recent education only"
                )
                break

            # Fill each field for this entry
            for yaml_key, id_template in edu_field_keys:
                value = edu.get(yaml_key)
                if not value:
                    continue
                value = str(value)
                field_id = id_template.format(idx=i)

                try:
                    locator = self._page.locator(f"#{field_id}")
                    if await locator.count() == 0:
                        continue

                    # Check if it's a combobox/select (has role=combobox)
                    is_combobox = await locator.evaluate(
                        "el => el.getAttribute('role') === 'combobox' || "
                        "el.getAttribute('aria-autocomplete') !== null"
                    )

                    if is_combobox:
                        label_text = yaml_key.replace("_", " ").title()
                        await self._select_best_option(
                            locator, f"{label_text} (entry {i})", value, required=False
                        )
                    else:
                        await locator.fill(value, timeout=self._LOCATE_TIMEOUT)

                    submitted_values.append({
                        "form_label": f"{yaml_key} (education {i + 1})",
                        "mapped_to": f"education_{yaml_key}_{i}",
                        "value": value,
                    })
                    logger.debug(f"[education] Filled {field_id} = {value[:30]}")
                except Exception as exc:
                    logger.warning(f"[education] Failed to fill {field_id}: {exc}")

            # Brief settle for React re-render
            await self._page.wait_for_timeout(500)

    async def _resolve_locator(self, label: str, form_field: FormField | None):
        """
        Tries locators in priority order. Returns the first one that resolves
        to a visible element. Raises _FieldNotFoundError if none match.
        """
        candidates = []

        if form_field:
            if form_field.element_id:
                candidates.append(self._page.locator(f"#{form_field.element_id}"))
            if form_field.name:
                candidates.append(self._page.locator(f"[name='{form_field.name}']"))
            if form_field.placeholder:
                candidates.append(
                    self._page.get_by_placeholder(form_field.placeholder, exact=False)
                )

        candidates.append(self._page.get_by_label(label, exact=False))
        candidates.append(self._page.get_by_placeholder(label, exact=False))

        for loc in candidates:
            try:
                # Use .first to avoid strict-mode errors on multi-match
                resolved = loc.first
                await resolved.wait_for(state="visible", timeout=self._LOCATE_TIMEOUT)
                return resolved
            except Exception:
                continue

        raise _FieldNotFoundError(label)

    async def submit(self, email: str | None = None, run_id: str | None = None) -> tuple[str | None, str | None]:
        """
        Clicks the primary submit button and waits for a confirmation signal.
        Returns (confirmation_url, confirmation_text).

        Greenhouse may respond in two ways:
          - Full navigation (classic flow): a new page loads after submit.
          - In-place confirmation (React SPA): the form is replaced by a success
            message without any navigation event.
        We try navigation first; if none occurs within the timeout we fall back
        to waiting for networkidle, which covers the SPA case.

        Some Greenhouse forms require an email verification code after clicking
        submit.  If an ``#email-verification`` fieldset appears, the code is
        fetched from Gmail using *email* (the address the code was sent to)
        and entered into the individual character inputs.

        Raises RuntimeError if the form is still present after clicking submit,
        which indicates validation errors (Greenhouse scrolled to the first
        unfilled required field without actually submitting).
        """
        # Scope submit button search to the Greenhouse form container when
        # on an embedded page — avoids matching the host site's own submit
        # buttons (e.g. a header search icon).
        form_scope = self._page.locator("#grnhse_app")
        if await form_scope.count() == 0:
            form_scope = self._page

        submit_btn = form_scope.get_by_role("button", name="Submit Application", exact=False)
        fallback_btn = form_scope.locator("input[type='submit'], button[type='submit']").first
        # Broader fallback: any button containing "submit" in its text
        text_btn = form_scope.locator("button:has-text('submit'), input[value*='submit' i]").first

        if await submit_btn.count() > 0:
            btn = submit_btn
        elif await fallback_btn.count() > 0:
            btn = fallback_btn
        elif await text_btn.count() > 0:
            btn = text_btn
        else:
            raise RuntimeError("Could not find submit button on page")

        # Guard: make sure we didn't grab a non-submit button (e.g. "Apply Now")
        try:
            btn_text = (await btn.text_content(timeout=5_000) or "").strip().lower()
        except Exception:
            btn_text = ""
        if btn_text and "submit" not in btn_text:
            logger.warning(f"[submit] Matched button has text {btn_text!r} — falling back to stricter search")
            if await text_btn.count() > 0:
                btn = text_btn

        # ── Diagnostic: capture submit request/response ──────────────────────
        captured_requests: list[dict] = []

        async def _on_request(request):
            if request.method == "POST" and request.url:
                entry = {
                    "url": request.url,
                    "method": request.method,
                    "post_data": request.post_data[:2000] if request.post_data else None,
                }
                captured_requests.append(entry)
                logger.debug(f"[submit] Captured POST → {request.url}")
                if request.post_data:
                    has_recaptcha = "recaptcha" in request.post_data.lower() or "g-recaptcha" in request.post_data.lower()
                    logger.debug(f"[submit]   reCAPTCHA token present: {has_recaptcha}")
                    logger.debug(f"[submit]   payload preview: {request.post_data[:500]}")

        async def _on_response(response):
            if response.request.method == "POST" and response.status >= 400:
                try:
                    body = await response.text()
                except Exception:
                    body = "(could not read body)"
                logger.warning(
                    f"[submit] POST {response.url} → {response.status}\n"
                    f"[submit]   response: {body[:500]}"
                )

        self._page.on("request", _on_request)
        self._page.on("response", _on_response)

        # Also check reCAPTCHA state before clicking
        recaptcha_info = await self._page.evaluate("""() => {
            const info = {};
            info.grecaptchaExists = typeof grecaptcha !== 'undefined';
            try { info.sitekey = document.querySelector('.g-recaptcha, [data-sitekey]')?.getAttribute('data-sitekey') || null; } catch(e) {}
            try { info.tokenField = document.querySelector('[name="g-recaptcha-response"]')?.value?.slice(0, 40) || null; } catch(e) {}
            try { info.tokenFieldLength = document.querySelector('[name="g-recaptcha-response"]')?.value?.length || 0; } catch(e) {}
            return info;
        }""")
        logger.info(f"[submit] reCAPTCHA state before click: {recaptcha_info}")

        pre_submit_url = self._page.url
        await btn.click()

        # Wait for the page to signal completion: either the URL changes (full
        # navigation), the submit button disappears (SPA in-place swap), or
        # an email verification challenge appears.
        try:
            await self._page.wait_for_function(
                """([url]) => document.location.href !== url
                    || document.querySelector('#email-verification') !== null
                    || document.querySelector(
                        'button[aria-label="Submit Application"], '
                        + 'input[type="submit"], button[type="submit"]'
                    ) === null""",
                arg=[pre_submit_url],
                timeout=25_000,
            )
        except Exception:
            # Timed out — fall through to the presence check below.
            pass

        # Belt-and-suspenders: ensure any in-flight XHR/fetch has settled.
        try:
            await self._page.wait_for_load_state("networkidle", timeout=7_000)
        except Exception:
            pass

        logger.info(f"[submit] Captured {len(captured_requests)} POST request(s) after first submit click")
        for i, req in enumerate(captured_requests):
            logger.info(f"[submit]   [{i}] {req['url']}")

        # ── Email verification challenge ─────────────────────────────────────
        verification_fieldset = self._page.locator("#email-verification")
        if await verification_fieldset.count() > 0 and await verification_fieldset.is_visible():
            await self._handle_email_verification(email)
            if run_id:
                from yahbah.browser.manager import BrowserRegistry
                await BrowserRegistry.instance().screenshot(run_id, "after_verification")
            # After entering the code Greenhouse may auto-submit or we need
            # to click submit again.
            try:
                await self._page.wait_for_function(
                    """([url]) => document.location.href !== url
                        || document.querySelector(
                            'button[aria-label="Submit Application"], '
                            + 'input[type="submit"], button[type="submit"]'
                        ) === null""",
                    arg=[pre_submit_url],
                    timeout=10_000,
                )
            except Exception:
                # Didn't auto-submit — click submit again.
                btn = submit_btn if await submit_btn.count() > 0 else fallback_btn
                await btn.click()
                try:
                    await self._page.wait_for_function(
                        """([url]) => document.location.href !== url
                            || document.querySelector(
                                'input[type="submit"], button[type="submit"]'
                            ) === null""",
                        arg=[pre_submit_url],
                        timeout=25_000,
                    )
                except Exception:
                    pass

            try:
                await self._page.wait_for_load_state("networkidle", timeout=10_000)
            except Exception:
                pass

        # --- Verify the submission actually succeeded ---
        # If the submit button is still visible the form bounced us back (Greenhouse
        # scrolls to the first invalid required field without submitting).
        # Before giving up, perform a remediation pass: check any newly-visible
        # required checkboxes and then retry submit once.
        submit_still_present = (
            await submit_btn.count() > 0 and await submit_btn.is_visible()
        ) or (
            await fallback_btn.count() > 0 and await fallback_btn.is_visible()
        )
        if submit_still_present:
            remediated = await self._remediate_and_retry(submit_btn, fallback_btn, pre_submit_url)
            if not remediated:
                # Surface the first visible validation error for diagnostics.
                error_loc = self._page.locator(
                    ".error, .field_error, [aria-invalid='true'], .invalid-feedback, "
                    ".helper-text--error"
                )
                error_hint = ""
                for i in range(min(await error_loc.count(), 10)):
                    try:
                        if await error_loc.nth(i).is_visible():
                            error_hint = (await error_loc.nth(i).inner_text()).strip()
                            break
                    except Exception:
                        pass
                msg = "Form submission failed — submit button still visible after click"
                if error_hint:
                    msg += f" (first error: {error_hint!r})"
                raise RuntimeError(msg)

        self._page.remove_listener("request", _on_request)
        self._page.remove_listener("response", _on_response)

        confirmation_url = self._page.url

        # Capture both plain text and HTML for rich rendering
        confirmation_text = await self._page.inner_text("body")

        # Try to get the main content HTML (not full page boilerplate)
        confirmation_html = ""
        for sel in ["#content", "main", ".content", "body"]:
            el = await self._page.query_selector(sel)
            if el:
                html = await el.inner_html()
                if len(html) > 50:
                    confirmation_html = html
                    break

        # Extract tracking/status URL if present
        tracking_url = await self._page.evaluate("""() => {
            const links = document.querySelectorAll('a');
            for (const a of links) {
                const text = (a.textContent || '').toLowerCase();
                const href = a.href || '';
                if ((text.includes('track') || text.includes('status') || text.includes('mygreen') || text.includes('sign in'))
                    && href && !href.includes('javascript:') && href !== window.location.href) {
                    return href;
                }
            }
            return null;
        }""")

        return confirmation_url, confirmation_text[:2000], confirmation_html[:4000], tracking_url

    async def _handle_email_verification(self, email: str | None = None) -> None:
        """
        Fills the 8-character email verification code into the individual
        ``security-input-N`` fields.

        Greenhouse renders 8 single-character ``<input maxlength="1">`` elements
        inside ``#email-verification``.  Typing into the first one auto-advances
        focus to the next, so we use ``press_sequentially`` on the first input
        and let the page handle the rest.
        """
        if email is None:
            raise RuntimeError(
                "Greenhouse requires email verification but no email was provided"
            )

        # TODO clean up this code once we have a real implementation
        #  move this import up out of this function
        from yahbah.gmail.client import GmailClient

        logger.info(f"Email verification challenge detected — polling Gmail for code sent to {email}")
        gmail = GmailClient()
        code = await gmail.fetch_verification_code(email)
        code = code.strip()
        if len(code) != 8:  # todo don't hardcode this to be 8, it could be more or less
            raise RuntimeError(
                f"Expected 8-character verification code, got {len(code)}: {code!r}"
            )

        # Focus the first input and type the full code — each character
        # auto-advances to the next input via Greenhouse's JS handler.
        first_input = self._page.locator("#security-input-0")
        await first_input.wait_for(state="visible", timeout=5_000)
        await first_input.click()
        await first_input.press_sequentially(code, delay=80)

        # Verify all 8 inputs were filled
        for i in range(8):
            val = await self._page.locator(f"#security-input-{i}").input_value()
            if not val:
                # Auto-advance didn't work — fill each input individually
                logger.warning("Auto-advance failed — filling inputs individually")
                for j in range(8):
                    inp = self._page.locator(f"#security-input-{j}")
                    await inp.fill(code[j])
                break

        logger.info("Verification code entered")

    async def _remediate_and_retry(self, submit_btn, fallback_btn, pre_submit_url: str) -> bool:
        """
        Called when the first submit attempt failed (form bounced back).
        Scans for any newly-visible required checkboxes that were injected
        dynamically (e.g. GDPR demographic consent) and checks them, then
        retries submit once.
        Returns True if the retry appears to have succeeded.
        """
        logger.warning("Submit bounced — running remediation scan before retry")

        # Check any visible required checkboxes that are still unchecked.
        fixed_any = False
        handles = await self._page.query_selector_all(
            "input[type='checkbox'][required]:not([aria-hidden='true']), "
            "input[type='checkbox'][aria-required='true']:not([aria-hidden='true'])"
        )
        for handle in handles:
            try:
                if not await handle.is_visible():
                    continue
                if await handle.is_checked():
                    continue
                name = await handle.get_attribute("name") or await handle.get_attribute("id") or "?"
                await handle.check()
                logger.info(f"Remediation: checked required checkbox {name!r}")
                fixed_any = True
            except Exception as exc:
                logger.warning(f"Remediation: could not check checkbox: {exc}")

        if not fixed_any:
            logger.warning("Remediation: no unchecked checkboxes found — giving up")
            return False

        # Retry submit.
        btn = submit_btn if await submit_btn.count() > 0 else fallback_btn
        await btn.click()

        try:
            await self._page.wait_for_function(
                """([url]) => document.location.href !== url
                    || document.querySelector(
                        'input[type="submit"], button[type="submit"]'
                    ) === null""",
                arg=[pre_submit_url],
                timeout=25_000,
            )
        except Exception:
            pass

        try:
            await self._page.wait_for_load_state("networkidle", timeout=7_000)
        except Exception:
            pass

        still_present = (
            await submit_btn.count() > 0 and await submit_btn.is_visible()
        ) or (
            await fallback_btn.count() > 0 and await fallback_btn.is_visible()
        )
        if still_present:
            logger.error("Remediation retry also failed — form still present")
            return False

        logger.info("Remediation retry succeeded — form submitted")
        return True
