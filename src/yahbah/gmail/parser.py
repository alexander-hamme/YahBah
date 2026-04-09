"""
Email body parsing and verification code extraction.

Extraction strategy:
  1. Heuristic: Greenhouse emails put the code in an <h1> tag.
  2. Fallback: LLM extracts the code from the plain-text body.
"""
import base64
import re
from html.parser import HTMLParser

from loguru import logger
from pydantic import BaseModel

from yahbah.llm.client import OllamaClient


class _ExtractedCode(BaseModel):
    code: str


class EmailStatusClassification(BaseModel):
    is_status_update: bool
    status_type: str
    confidence: float
    summary: str
    company_name: str | None = None


class _H1Extractor(HTMLParser):
    """Extracts text content of the first <h1> tag."""

    def __init__(self) -> None:
        super().__init__()
        self._in_h1 = False
        self.text: str | None = None

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag == "h1" and self.text is None:
            self._in_h1 = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "h1":
            self._in_h1 = False

    def handle_data(self, data: str) -> None:
        if self._in_h1 and self.text is None:
            self.text = data.strip()


def decode_gmail_body(data: str) -> str:
    """Decodes a base64url-encoded Gmail message body part."""
    return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")


def get_body_parts(payload: dict) -> tuple[str | None, str | None]:
    """
    Recursively walks a Gmail message payload and returns (html, plain_text).
    """
    html: str | None = None
    plain: str | None = None

    mime = payload.get("mimeType", "")

    if mime == "text/html":
        data = payload.get("body", {}).get("data", "")
        if data:
            html = decode_gmail_body(data)
    elif mime == "text/plain":
        data = payload.get("body", {}).get("data", "")
        if data:
            plain = decode_gmail_body(data)

    for part in payload.get("parts", []):
        sub_html, sub_plain = get_body_parts(part)
        html = html or sub_html
        plain = plain or sub_plain

    return html, plain


def extract_code_heuristic(html: str) -> str | None:
    """
    Tries to extract a verification code from HTML using simple heuristics.

    Greenhouse puts the code inside an <h1> tag. The code is typically
    8 alphanumeric characters.
    """
    parser = _H1Extractor()
    parser.feed(html)

    if parser.text and re.fullmatch(r"[A-Za-z0-9]{4,12}", parser.text.strip()):
        return parser.text.strip()

    return None


async def extract_code_llm(text: str) -> str:
    """
    Uses the LLM to extract a verification/security code from email text.

    Raises LLMError if extraction fails.
    """
    llm = OllamaClient()
    result = await llm.generate_structured(
        system_prompt=(
            "You are extracting a security or verification code from an email. "
            "The code is typically 4-12 alphanumeric characters. "
            "Return ONLY the code, nothing else. "
            'Respond with valid JSON: {"code": "<the_code>"}'
        ),
        user_prompt=f"Extract the verification code from this email:\n\n{text[:2000]}",
        response_model=_ExtractedCode,
    )
    return result.code.strip()


async def classify_status_email(
    subject: str, sender: str, body_text: str
) -> EmailStatusClassification:
    """Classify a post-application email using the LLM."""
    from yahbah.config import load_prompts_config

    llm = OllamaClient()
    prompt = load_prompts_config()["prompts"]["email_status_classification"]
    user_prompt = (
        f"Subject: {subject}\n"
        f"From: {sender}\n\n"
        f"Body:\n{body_text[:3000]}"
    )
    return await llm.generate_structured(
        system_prompt=prompt,
        user_prompt=user_prompt,
        response_model=EmailStatusClassification,
    )


async def extract_verification_code(html: str | None, plain_text: str | None) -> str:
    """
    Extracts a verification code from email content.

    Tries heuristic extraction from HTML first, falls back to LLM.
    """
    if html:
        code = extract_code_heuristic(html)
        if code:
            logger.debug(f"Verification code extracted via heuristic: {code}")
            return code

    # Fall back to LLM — prefer plain text (less noise), use HTML stripped if needed
    body = plain_text or html or ""
    if not body:
        raise ValueError("Email has no body content to extract code from")

    logger.debug("Heuristic extraction failed — falling back to LLM")
    code = await extract_code_llm(body)
    logger.debug(f"Verification code extracted via LLM: {code}")
    return code
