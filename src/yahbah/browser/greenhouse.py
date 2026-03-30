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
    selected: str


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
        await self._page.wait_for_load_state("networkidle", timeout=15_000)
        await self.validate_application_page()

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
        if field_type in ("button", "reset", "image", "checkbox"):
            # Skip non-text inputs for now except file
            if field_type != "file":
                return None

        # Skip invisible non-file inputs — they are hidden shadow/duplicate
        # elements (e.g. the second hidden copy of custom React dropdown fields).
        if field_type != "file" and not await handle.is_visible():
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
        await self._page.wait_for_load_state("networkidle", timeout=15_000)

        for mapping in field_mappings:
            if mapping.confidence < MIN_CONFIDENCE:
                if mapping.mapped_to not in ("cover_letter", "resume"):
                    logger.warning(
                        f"Skipping '{mapping.form_label}' — confidence {mapping.confidence:.2f} below threshold"
                    )
                    continue

            form_field = self._field_by_label.get(mapping.form_label)
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
                logger.error(f"Failed to fill '{mapping.form_label}': {exc}")
                raise

            # Best-effort settle wait — lets React/Vue controlled inputs re-render.
            # Timeout is intentionally swallowed; missing it just risks a stale screenshot.
            try:
                await self._page.wait_for_load_state("domcontentloaded", timeout=2_000)
            except Exception:
                pass

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
            await self._upload_file(
                self._page.locator("input[type='file']").first, value
            )
            logger.debug(f"Uploaded resume: {value}", )
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
                        if (el.getAttribute('role') === 'combobox'
                            || el.getAttribute('aria-haspopup')) return 'combobox';
                        let node = el.parentElement;
                        while (node) {
                            if (node.getAttribute('role') === 'combobox') return 'combobox';
                            node = node.parentElement;
                        }
                        return 'input';
                    }""",
                    timeout=self._LOCATE_TIMEOUT,
                )
                is_select = dom_kind in ("select", "combobox")
            except Exception:
                is_select = False

        if is_select:
            await self._select_best_option(locator, mapping.form_label, value)
        else:
            await locator.fill(value, timeout=self._LOCATE_TIMEOUT)
            printable = value if len(value) <= 40 else f"{value[:40]}–"
            logger.debug(f"Filled '{mapping.form_label}' → '{printable}'")

    async def _select_best_option(self, locator, field_label: str, value: str) -> None:
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
            await self._page.wait_for_timeout(400)

            opt_loc = self._page.locator(_CUSTOM_OPT)

            # 2. Detect whether visible options appeared immediately (regular
            #    dropdown) or whether typing is required first (autocomplete).
            #    Only scan the first 30 candidates to keep this fast.
            has_visible = False
            for i in range(min(await opt_loc.count(), 30)):
                try:
                    if await opt_loc.nth(i).is_visible():
                        has_visible = True
                        break
                except Exception:
                    pass

            if not has_visible:
                # Autocomplete — type the primary search term.
                # Use only the part before the first comma so that "Boston, MA"
                # becomes "Boston" and triggers city-name matches.
                search_term = value.split(",")[0].strip()
                await locator.fill(search_term, timeout=self._LOCATE_TIMEOUT)
                await self._page.wait_for_timeout(600)

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
                logger.warning(f"No dropdown options found after clicking '{field_label}'")
                await self._page.keyboard.press("Escape")
                return

            option_texts = list(option_map.keys())

        if not option_texts:
            logger.warning(f"No options found for '{field_label}'")
            return

        # ── LLM picks best option ─────────────────────────────────────────────
        result = await self._llm.generate_structured(
            system_prompt=(
                "You are selecting the best matching option from a dropdown. "
                "Return ONLY the exact option text that best matches the intended answer. "
                'Respond with valid JSON: {"selected": "<exact option text>"}'
            ),
            user_prompt=(
                f'Field: "{field_label}"\n'
                f'Intended answer: "{value}"\n'
                "Available options:\n"
                + "\n".join(f"  - {t}" for t in option_texts)
            ),
            response_model=_SelectPick,
        )

        if result.selected not in option_texts:
            logger.warning(
                f"LLM picked '{result.selected}' which is not in options for '{field_label}' — skipping"
            )
            if tag != "select":
                await self._page.keyboard.press("Escape")
            return

        if tag == "select":
            await locator.select_option(label=result.selected, timeout=self._LOCATE_TIMEOUT)
        else:
            await opt_loc.nth(option_map[result.selected]).click(timeout=self._LOCATE_TIMEOUT)

        logger.debug(f"Selected '{result.selected}' for '{field_label}'")

    async def _upload_file(self, locator, file_path: str) -> None:
        """
        Uploads a file via the OS file-chooser interception pattern.

        Custom upload components (e.g. Greenhouse's React uploader) crash when
        set_input_files() is called directly because they expect their own click
        handler to run first. expect_file_chooser() lets us click-trigger the
        component normally, intercept the dialog the browser would show, and
        inject the file — all without the OS dialog appearing.

        Falls back to set_input_files() for plain <input type="file"> elements
        that don't trigger a chooser event.
        """
        await locator.wait_for(state="attached", timeout=self._UPLOAD_TIMEOUT)
        try:
            async with self._page.expect_file_chooser(
                timeout=self._LOCATE_TIMEOUT
            ) as fc_info:
                await locator.evaluate("node => node.click()")
            chooser = await fc_info.value
            await chooser.set_files(file_path)
        except Exception:
            # Plain input — no chooser event fired; fall back to direct inject.
            await locator.set_input_files(file_path, timeout=self._LOCATE_TIMEOUT)

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
        """
        submit_locator = self._page.get_by_role("button", name="Submit Application")
        fallback_locator = self._page.locator(
            "input[type='submit'], button[type='submit']"
        ).first

        btn = submit_locator if await submit_locator.count() > 0 else fallback_locator

        try:
            async with self._page.expect_navigation(
                timeout=15_000, wait_until="domcontentloaded"
            ):
                await btn.click()
        except Exception:
            # No navigation occurred — SPA in-place confirmation.
            # Wait for any pending network activity to settle.
            try:
                await self._page.wait_for_load_state("networkidle", timeout=10_000)
            except Exception:
                pass

        confirmation_url = self._page.url
        confirmation_text = await self._page.inner_text("body")
        return confirmation_url, confirmation_text[:2000]
