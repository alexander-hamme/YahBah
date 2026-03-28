"""
Greenhouse-specific form extraction, auth handling, and deterministic filling.

Extraction: reads the DOM to produce a FormSchema.
Auth: detects login/signup walls and creates an account with generated credentials.
Filling: takes FieldMappings and applies them via Playwright locators.
No LLM involvement here.
"""
from loguru import logger
from playwright.async_api import Page

from yahbah.schemas import FieldMapping, FormField, FormSchema


# Confidence threshold below which we refuse to fill a required field
MIN_CONFIDENCE = 0.7


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
                logger.debug("Navigated to sign-up form via '%s'", selector)
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

        logger.info("Account created for %s", email)


class GreenhouseExtractor:
    """
    Extracts all interactive form fields from a Greenhouse application page.

    Greenhouse forms live inside <div id="application_form"> (or similar).
    We locate all input, select, and textarea elements, resolve their labels,
    and return a FormSchema.
    """

    def __init__(self, page: Page) -> None:
        self._page = page

    async def extract(self) -> tuple[FormSchema, str]:
        """
        Returns (FormSchema, job_description_text).
        job_description is scraped from the page for the cover letter prompt.
        """
        await self._page.wait_for_load_state("networkidle", timeout=15_000)

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

        logger.info("Extracted %d form fields", len(fields))

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

    _LOCATE_TIMEOUT = 3_000  # ms per locator attempt

    def __init__(self, page: Page, form_schema: FormSchema | None = None) -> None:
        self._page = page
        # label → FormField lookup for precise targeting
        self._field_by_label: dict[str, FormField] = (
            {f.label: f for f in form_schema.fields} if form_schema else {}
        )

    async def fill(
        self,
        field_mappings: list[FieldMapping],
        cover_letter_path: str | None,
    ) -> None:
        for mapping in field_mappings:
            if mapping.confidence < MIN_CONFIDENCE:
                if mapping.mapped_to not in ("cover_letter", "resume"):
                    logger.warning(
                        "Skipping '%s' — confidence %.2f below threshold",
                        mapping.form_label, mapping.confidence,
                    )
                    continue

            form_field = self._field_by_label.get(mapping.form_label)
            try:
                await self._fill_field(mapping, form_field, cover_letter_path)
            except _FieldNotFoundError:
                if form_field and form_field.required:
                    raise RuntimeError(
                        f"Could not locate required field '{mapping.form_label}'"
                    )
                logger.warning("Could not locate optional field '%s' — skipping", mapping.form_label)
            except Exception as exc:
                logger.error("Failed to fill '%s': %s", mapping.form_label, exc)
                raise

    async def _fill_field(
        self,
        mapping: FieldMapping,
        form_field: FormField | None,
        cover_letter_path: str | None,
    ) -> None:
        value = mapping.value
        mapped_to = mapping.mapped_to

        # ── File uploads ──────────────────────────────────────────────────────
        if mapped_to == "resume":
            file_input = self._page.locator("input[type='file']").first
            await file_input.set_input_files(value, timeout=self._LOCATE_TIMEOUT)
            logger.debug("Uploaded resume: %s", value)
            return

        if mapped_to == "cover_letter" and cover_letter_path:
            file_inputs = self._page.locator("input[type='file']")
            if await file_inputs.count() > 1:
                await file_inputs.nth(1).set_input_files(
                    cover_letter_path, timeout=self._LOCATE_TIMEOUT
                )
                logger.debug("Uploaded cover letter: %s", cover_letter_path)
                return
            # Fall through to text fill if no second file input

        # ── Build locator chain ───────────────────────────────────────────────
        locator = await self._resolve_locator(mapping.form_label, form_field)

        # Determine tag to decide fill vs select_option
        try:
            tag = await locator.evaluate(
                "el => el.tagName.toLowerCase()", timeout=self._LOCATE_TIMEOUT
            )
        except Exception:
            tag = "input"

        fill_value = cover_letter_path if mapped_to == "cover_letter" else value

        if tag == "select":
            try:
                await locator.select_option(label=fill_value, timeout=self._LOCATE_TIMEOUT)
            except Exception:
                # Try selecting by value if label match fails
                await locator.select_option(value=fill_value, timeout=self._LOCATE_TIMEOUT)
        else:
            await locator.fill(fill_value, timeout=self._LOCATE_TIMEOUT)

        logger.debug("Filled '%s' → '%s'", mapping.form_label, fill_value[:40])

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
        Clicks the primary submit button and waits for navigation.
        Returns (confirmation_url, confirmation_text).
        """
        submit_locator = (
            self._page.get_by_role("button", name="Submit Application")
        )
        fallback_locator = self._page.locator(
            "input[type='submit'], button[type='submit']"
        ).first

        btn = submit_locator if await submit_locator.count() > 0 else fallback_locator

        async with self._page.expect_navigation(timeout=15_000, wait_until="domcontentloaded"):
            await btn.click()

        confirmation_url = self._page.url
        confirmation_text = await self._page.inner_text("body")
        return confirmation_url, confirmation_text[:2000]
