"""
Greenhouse-specific form extraction and deterministic filling.

Extraction: reads the DOM to produce a FormSchema.
Filling: takes FieldMappings and applies them via Playwright locators.
No LLM involvement here.
"""
from loguru import logger
from playwright.async_api import Page

from yahbah.schemas import FieldMapping, FormField, FormSchema


# Confidence threshold below which we refuse to fill a required field
MIN_CONFIDENCE = 0.7


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
    No LLM involvement — pure Playwright.
    """

    def __init__(self, page: Page) -> None:
        self._page = page

    async def fill(
        self,
        field_mappings: list[FieldMapping],
        cover_letter_path: str | None,
    ) -> None:
        for mapping in field_mappings:
            if mapping.confidence < MIN_CONFIDENCE:
                if mapping.mapped_to not in ("cover_letter", "resume"):
                    logger.warning(
                        "Skipping field '%s' — confidence %.2f below threshold",
                        mapping.form_label,
                        mapping.confidence,
                    )
                    continue

            try:
                await self._fill_field(mapping, cover_letter_path)
            except Exception as exc:
                logger.error(
                    "Failed to fill field '%s': %s", mapping.form_label, exc
                )
                raise

    async def _fill_field(
        self,
        mapping: FieldMapping,
        cover_letter_path: str | None,
    ) -> None:
        label = mapping.form_label
        value = mapping.value
        mapped_to = mapping.mapped_to

        # Locate by label text first, then by placeholder
        locator = (
            self._page.get_by_label(label, exact=False)
            or self._page.get_by_placeholder(label, exact=False)
        )

        if mapped_to == "resume":
            # File upload — locate the file input
            file_input = self._page.locator("input[type='file']").first
            await file_input.set_input_files(value)
            logger.debug("Uploaded resume: %s", value)
            return

        if mapped_to == "cover_letter" and cover_letter_path:
            # Cover letter can be a file upload or a textarea
            file_inputs = self._page.locator("input[type='file']")
            count = await file_inputs.count()
            if count > 1:
                # Second file input is usually cover letter
                await file_inputs.nth(1).set_input_files(cover_letter_path)
                logger.debug("Uploaded cover letter file: %s", cover_letter_path)
                return
            # Otherwise fill it as text in a textarea
            await self._fill_text_or_textarea(label, cover_letter_path)
            return

        # Determine element type and fill accordingly
        tag = await self._get_tag(label)
        if tag == "select":
            await self._page.get_by_label(label, exact=False).select_option(label=value)
        else:
            await self._fill_text_or_textarea(label, value)

        logger.debug("Filled '%s' → '%s'", label, value[:40] if value else "")

    async def _fill_text_or_textarea(self, label: str, value: str) -> None:
        loc = self._page.get_by_label(label, exact=False)
        await loc.first.fill(value)

    async def _get_tag(self, label: str) -> str:
        loc = self._page.get_by_label(label, exact=False)
        try:
            tag = await loc.first.evaluate("el => el.tagName.toLowerCase()")
            return tag
        except Exception:
            return "input"

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
