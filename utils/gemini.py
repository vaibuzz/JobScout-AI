"""
Gemini API wrapper — Mesa Careers AI.

Uses google-genai SDK (NOT the deprecated google-generativeai).
Key advantage: native Pydantic response_schema support via response.parsed.
No manual JSON parsing or regex fence stripping needed.

Model strategy:
    MODEL_FLASH — gemini-2.5-flash  (default, used for all extraction/scoring tasks)
    MODEL_PRO   — gemini-2.5-pro    (used for S2 synthesis, S5 outreach, S6 dossier)

Public API:
    gemini_text(prompt, system, model, thinking) -> str
    gemini_json(prompt, system, schema, model, thinking) -> <Pydantic model instance>
    gemini_extract_salary_lpa(text) -> int | None
"""
import os
import logging
from typing import Type, TypeVar

from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from pydantic import BaseModel

load_dotenv()

log = logging.getLogger(__name__)

# ── Model constants ───────────────────────────────────────────────────────────
MODEL_FLASH = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")   # fast, cheap — default
MODEL_PRO   = "gemini-2.5-pro"                                 # S2 synthesis, S5 outreach, S6 dossier

# ── Client singleton ──────────────────────────────────────────────────────────
_client = None

def _get_client():
    global _client
    if _client is None:
        from google import genai
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise EnvironmentError("GEMINI_API_KEY not set in environment")
        _client = genai.Client(api_key=api_key)
    return _client

T = TypeVar("T", bound=BaseModel)

# ── Retry config ──────────────────────────────────────────────────────────────
_retry = retry(
    reraise=True,
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(Exception),
)


@_retry
def gemini_text(
    prompt:   str,
    system:   str  = "",
    model:    str  = None,
    thinking: bool = False,
) -> str:
    """
    Call Gemini and return raw text response.
    Retries up to 3 times with exponential backoff.

    Args:
        prompt:   User message
        system:   Optional system instruction
        model:    Override model (default: MODEL_FLASH)
        thinking: Enable Gemini thinking mode for deeper reasoning

    Returns:
        Raw text string from Gemini
    """
    from google import genai
    client     = _get_client()
    model      = model or MODEL_FLASH
    full_prompt = f"{system}\n\n{prompt}" if system else prompt

    config = None
    if thinking:
        config = genai.types.GenerateContentConfig(
            thinking_config=genai.types.ThinkingConfig(thinking_budget=4096),
        )

    response = client.models.generate_content(
        model    = model,
        contents = full_prompt,
        config   = config,
    )
    return response.text.strip()


@_retry
def gemini_json(
    prompt:   str,
    system:   str,
    schema:   Type[T],
    model:    str  = None,
    thinking: bool = False,
) -> T:
    """
    Call Gemini with a Pydantic response schema.
    Returns a fully validated Pydantic model instance — no manual JSON parsing.

    Args:
        prompt:   User message
        system:   System instruction (guide Gemini on task context)
        schema:   Pydantic BaseModel class defining the expected output structure
        model:    Override model (default: MODEL_FLASH)
        thinking: Enable Gemini thinking mode for deeper reasoning (S2 synthesis only)

    Returns:
        Validated instance of `schema`

    Raises:
        ValueError: If Gemini returns data that fails Pydantic validation after retries
    """
    from google import genai
    client      = _get_client()
    model       = model or MODEL_FLASH
    full_prompt = f"{system}\n\n{prompt}" if system else prompt

    config_kwargs: dict = {
        "response_mime_type": "application/json",
        "response_schema":    schema,
    }
    if thinking:
        config_kwargs["thinking_config"] = genai.types.ThinkingConfig(thinking_budget=4096)

    response = client.models.generate_content(
        model    = model,
        contents = full_prompt,
        config   = genai.types.GenerateContentConfig(**config_kwargs),
    )

    result = response.parsed
    if result is None:
        # Fallback: try manual parse if .parsed is None (rare edge case)
        import json, re
        raw = response.text.strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw.strip())
        result = schema.model_validate_json(raw)

    log.debug("gemini_json OK | schema=%s model=%s", schema.__name__, model)
    return result


def gemini_extract_salary_lpa(text: str) -> int | None:
    """
    Extract a salary figure in LPA (Lakhs Per Annum) from free-form text.
    Used in Stage 2 (comp grounding) — intentionally uses Flash (simple extraction).

    Returns:
        Integer LPA figure (e.g. 28) or None if no salary data found
    """
    from pydantic import Field

    class _SalaryExtract(BaseModel):
        salary_lpa: int | None = Field(
            default=None,
            description="Annual salary in Indian Lakhs Per Annum (LPA). "
                        "E.g. '28 LPA' → 28. Return null if no clear salary mentioned.",
        )

    system = (
        "You are a salary data extractor for INDIAN job market data. "
        "Extract the annual base salary in Indian LPA (Lakhs Per Annum) from the text. "
        "RULES: "
        "(1) Only return values between 10 and 80 — realistic Indian startup base salaries. "
        "(2) If the text shows USD/GBP/EUR salaries, or values above 80, return null. "
        "(3) '28 LPA', '28 lakhs', '28L CTC' all map to 28. "
        "(4) Return null if no clear INR salary is found."
    )
    try:
        result = gemini_json(text, system, _SalaryExtract, model=MODEL_FLASH)
        return result.salary_lpa
    except Exception as e:
        log.warning("gemini_extract_salary_lpa failed: %s", e)
        return None
