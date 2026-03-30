"""
Greenhouse-specific form extraction, auth handling, and deterministic filling.

Extraction: reads the DOM to produce a FormSchema.
Auth: detects login/signup walls and creates an account with generated credentials.
Filling: takes FieldMappings and applies them via Playwright locators.
Select fields use an LLM fallback to pick the closest option when exact match fails.
"""
from loguru import logger
from playwright.async_api import Page
from pydantic import BaseModel

from yahbah.llm.client import OllamaClient
from yahbah.schemas import FieldMapping, FormField, FormSchema


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

    async def validate_application_page(self) -> None:
        """
        Raises ValueError if the current page is not a job application form.
        Catches the common case of a taken-down Greenhouse job that redirects
        to the company's job board (URL contains ?error=true, or the path no
        longer has a numeric job ID).
        """
        import re
        url = self._page.url
        if "error=true" in url:
            raise ValueError(f"Job posting no longer available (redirected to: {url})")

        # Greenhouse application URLs always end in /jobs/<numeric-id>
        if not re.search(r"/jobs/\d+", url):
            title = await self._page.title()
            raise ValueError(
                f"Page does not appear to be a job application form "
                f"(url={url!r}, title={title!r})"
            )

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

        logger.info(f"Extracted {len(fields)} form fields")

        page_url = self._page.url
        page_title = await self._page.title()
        job_description = await self._extract_job_description()

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

        # Skip the intl-tel-input country-code search widget — it shares the
        # "Phone" label but is not a fillable field.
        if await handle.evaluate("el => !!el.closest('.iti')"):
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
                    return text[:4000]  # cap for LLM prompt
        return await self._page.evaluate("document.body.innerText") or ""

    async def extract_job_metadata(self) -> tuple[str | None, str | None, str | None, str]:
        """
        Scrapes structured job metadata from the page.
        Returns (title, company, location, canonical_url).
        canonical_url is page.url after all redirects.
        """
        import re

        canonical_url = self._page.url

        async def _first_text(*selectors: str) -> str | None:
            for sel in selectors:
                try:
                    el = self._page.locator(sel).first
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
            page_title = await self._page.title()
            m = re.search(
                r"Apply(?:\s+for)?\s+(.+?)\s+at\s+(.+?)(?:\s*[|–—\-]|$)",
                page_title,
                re.IGNORECASE,
            )
            if m:
                title = title or m.group(1).strip()
                company = company or m.group(2).strip()

        return title, company, location, canonical_url


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
        page: Page,
        form_schema: FormSchema | None = None,
        llm_client: OllamaClient | None = None,
    ) -> None:
        self._page = page
        self._llm = llm_client or OllamaClient()
        # label → FormField lookup for precise targeting
        self._field_by_label: dict[str, FormField] = (
            {f.label: f for f in form_schema.fields} if form_schema else {}
        )

    async def fill(
        self,
        field_mappings: list[FieldMapping],
        cover_letter_path: str | None,
        cover_letter_text: str | None = None,
    ) -> None:
        # Ensure React/JS has fully hydrated before touching any field.
        # Without this, the first field(s) may be filled before event listeners
        # are attached, causing silent no-ops (especially on a fresh page load).
        logger.debug(f"[fill] Waiting for page load — URL: {self._page.url}")
        await self._page.wait_for_load_state("load", timeout=30_000)
        try:
            await self._page.wait_for_load_state("networkidle", timeout=5_000)
        except Exception:
            logger.debug("[fill] 'networkidle' timed out — proceeding anyway")

        seen_labels: set[str] = set()
        for mapping in field_mappings:
            # File uploads use well-known IDs, not labels — exempt from dedup
            if mapping.mapped_to not in ("resume", "cover_letter") and mapping.form_label in seen_labels:
                logger.debug(f"[fill] Skipping duplicate mapping for {mapping.form_label!r}")
                continue
            seen_labels.add(mapping.form_label)

            if mapping.confidence < MIN_CONFIDENCE:
                if mapping.mapped_to not in ("cover_letter", "resume"):
                    logger.warning(
                        f"Skipping '{mapping.form_label}' — confidence {mapping.confidence:.2f} below threshold"
                    )
                    continue

            form_field = self._field_by_label.get(mapping.form_label)
            logger.debug(
                f"[fill] Filling {mapping.form_label!r} "
                f"(mapped_to={mapping.mapped_to!r}, type={form_field.field_type if form_field else '?'}, "
                f"required={form_field.required if form_field else '?'})"
            )
            try:
                await self._fill_field(mapping, form_field, cover_letter_path, cover_letter_text)
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

            # Best-effort settle wait — lets React/Vue controlled inputs re-render.
            # Timeout is intentionally swallowed; missing it just risks a stale screenshot.
            try:
                await self._page.wait_for_load_state("domcontentloaded", timeout=2_000)
            except Exception:
                pass

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

    async def _fill_field(
        self,
        mapping: FieldMapping,
        form_field: FormField | None,
        cover_letter_path: str | None,
        cover_letter_text: str | None = None,
    ) -> None:
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
            # Prefer form_field metadata for precise targeting; the generic
            # first-file-input fallback is fragile when multiple inputs exist.
            if form_field and form_field.element_id:
                file_loc = self._page.locator(f"#{form_field.element_id}")
            elif form_field and form_field.name:
                file_loc = self._page.locator(f"[name='{form_field.name}']")
            else:
                file_loc = self._page.locator("input[type='file']").first
            await self._upload_file(file_loc, value)
            logger.debug(f"Uploaded resume: {value}")
            return

        if mapped_to == "cover_letter":
            # If the field is a file upload, upload the PDF.
            # If it's a text/textarea field, fill with the cover letter text directly.
            is_file_field = form_field is None or form_field.field_type == "file"

            if is_file_field:
                if not cover_letter_path:
                    logger.warning("Cover letter field is a file upload but no PDF available — skipping")
                    return
                # File inputs are hidden — _resolve_locator requires visible, so
                # target by form_field metadata with fallback to second file input.
                candidates = []
                if form_field:
                    if form_field.element_id:
                        candidates.append(self._page.locator(f"#{form_field.element_id}"))
                    if form_field.name:
                        candidates.append(self._page.locator(f"[name='{form_field.name}']"))
                candidates.append(self._page.locator("input[type='file']").nth(1))

                for loc in candidates:
                    try:
                        await loc.wait_for(state="attached", timeout=self._LOCATE_TIMEOUT)
                        await self._upload_file(loc, cover_letter_path)
                        logger.debug(f"Uploaded cover letter PDF: {cover_letter_path}")
                        return
                    except Exception:
                        continue

                raise _FieldNotFoundError(mapping.form_label)

            else:
                # Text/textarea field — paste the cover letter body directly
                if not cover_letter_text:
                    logger.warning("Cover letter field is text entry but no text available — skipping")
                    return
                locator = await self._resolve_locator(mapping.form_label, form_field)
                await locator.fill(cover_letter_text, timeout=self._LOCATE_TIMEOUT)
                logger.debug(f"Filled cover letter text into '{mapping.form_label}'")
                return

        # ── Checkboxes ────────────────────────────────────────────────────────
        if form_field and form_field.field_type == "checkbox":
            locator = await self._resolve_locator(mapping.form_label, form_field)
            await locator.check(timeout=self._LOCATE_TIMEOUT)
            logger.debug(f"Checked checkbox '{mapping.form_label}'")
            return

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
        else:
            await locator.fill(value, timeout=self._LOCATE_TIMEOUT)
            printable = value if len(value) <= 40 else f"{value[:40]}–"
            logger.debug(f"Filled '{mapping.form_label}' → '{printable}'")

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
            # 1. Click to open/focus the field.
            await locator.click(timeout=self._LOCATE_TIMEOUT)

            opt_loc = self._page.locator(_CUSTOM_OPT)

            # 2. Poll for visible options to appear (up to 2 s).
            #    A fixed wait is unreliable — React Select may take varying time
            #    to render options depending on page load and scroll position.
            visible_count = 0
            for _ in range(8):  # 8 × 250 ms = 2 s max
                await self._page.wait_for_timeout(250)
                visible_count = 0
                for i in range(min(await opt_loc.count(), 50)):
                    try:
                        if await opt_loc.nth(i).is_visible():
                            visible_count += 1
                    except Exception:
                        pass
                if visible_count > 0:
                    break

            # 3. Type a search term if:
            #   a) no options appeared at all (pure autocomplete), OR
            #   b) too many options visible (>15) — unfiltered list needs narrowing.
            if visible_count == 0:
                search_term = value.split(",")[0].strip()
                await locator.fill(search_term, timeout=self._LOCATE_TIMEOUT)
                # Poll again after typing
                for _ in range(8):
                    await self._page.wait_for_timeout(250)
                    visible_count = 0
                    for i in range(min(await opt_loc.count(), 50)):
                        try:
                            if await opt_loc.nth(i).is_visible():
                                visible_count += 1
                        except Exception:
                            pass
                    if visible_count > 0:
                        break

            # 3. Collect VISIBLE options only.
            #    Visibility filtering is critical: many pages keep large hidden
            #    option lists in the DOM (e.g. the iti phone-country picker with
            #    200+ entries) that would otherwise pollute the results and cause
            #    clicks on invisible elements.
            option_map: dict[str, int] = {}
            count = await opt_loc.count()
            for i in range(count):
                try:
                    if not await opt_loc.nth(i).is_visible():
                        continue
                    t = (await opt_loc.nth(i).inner_text()).strip()
                    if t and t not in option_map:
                        option_map[t] = i
                except Exception:
                    pass

            if not option_map:
                await self._page.keyboard.press("Escape")
                if required:
                    raise RuntimeError(
                        f"No dropdown options found for required field '{field_label}'"
                    )
                logger.warning(f"No dropdown options found after clicking '{field_label}'")
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
                    await self._page.keyboard.press("Escape")
                raise RuntimeError(f"{msg} (field is required)")
            logger.warning(f"{msg} — skipping")
            if tag != "select":
                await self._page.keyboard.press("Escape")
            return

        selected = option_texts[idx - 1]  # convert 1-based → 0-based

        if tag == "select":
            await locator.select_option(label=selected, timeout=self._LOCATE_TIMEOUT)
        else:
            await opt_loc.nth(option_map[selected]).click(timeout=self._LOCATE_TIMEOUT)
            # Close the dropdown — React Select multi-selects stay open after a
            # click, causing the next field to inherit leftover visible options.
            await self._page.keyboard.press("Escape")
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

    async def submit(self) -> tuple[str | None, str | None]:
        """
        Clicks the primary submit button and waits for a confirmation signal.
        Returns (confirmation_url, confirmation_text).

        Greenhouse may respond in two ways:
          - Full navigation (classic flow): a new page loads after submit.
          - In-place confirmation (React SPA): the form is replaced by a success
            message without any navigation event.
        We try navigation first; if none occurs within the timeout we fall back
        to waiting for networkidle, which covers the SPA case.

        Raises RuntimeError if the form is still present after clicking submit,
        which indicates validation errors (Greenhouse scrolled to the first
        unfilled required field without actually submitting).
        """
        submit_btn = self._page.get_by_role("button", name="Submit Application")
        fallback_btn = self._page.locator("input[type='submit'], button[type='submit']").first

        btn = submit_btn if await submit_btn.count() > 0 else fallback_btn

        pre_submit_url = self._page.url
        await btn.click()

        # Wait for the page to signal completion: either the URL changes (full
        # navigation) or the submit button disappears (SPA in-place swap).
        # This also enforces a minimum wait so slow Greenhouse backends have time
        # to process the submission before we inspect the result.
        try:
            await self._page.wait_for_function(
                """([url]) => document.location.href !== url
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

        confirmation_url = self._page.url
        confirmation_text = await self._page.inner_text("body")
        return confirmation_url, confirmation_text[:2000]

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
