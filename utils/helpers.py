"""
Shared utility helpers for Mesa Careers AI.

Functions:
    normalize(s)                      — Lowercase + strip noise words for dedup
    is_duplicate(lead, existing)      — rapidfuzz cross-channel dedup
    chunk_list(lst, size)             — Split list into batches
    strip_md_fences(text)             — Remove markdown fences (fallback only)
    recency_multiplier(posted_at)     — Score penalty for stale job listings
"""
import re
import logging
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger(__name__)


def normalize(s: str) -> str:
    """
    Lowercase and strip legal noise words for fuzzy dedup comparison.
    E.g. 'Acme Technologies Pvt. Ltd.' → 'acme'
    """
    s = s.lower().strip()
    noise = [
        "pvt ltd", "pvt. ltd.", "private limited", "technologies",
        "technology", "tech", "inc", "llp", "ltd", "limited",
        "solutions", "services", "india", "global",
    ]
    for word in noise:
        s = s.replace(word, " ")
    return re.sub(r"\s+", " ", s).strip()


def is_duplicate(new_lead: dict, existing_leads: list[dict]) -> bool:
    """
    Two-axis fuzzy dedup: company name (85%) + role title (80%).
    Uses rapidfuzz for efficient string comparison.

    Args:
        new_lead: Candidate lead dict with 'company_name' and 'role_title'
        existing_leads: Already accepted leads to compare against

    Returns:
        True if new_lead is a duplicate of any existing lead
    """
    from rapidfuzz import fuzz

    cn = normalize(new_lead.get("company_name", ""))
    rt = normalize(new_lead.get("role_title", ""))

    for ex in existing_leads:
        company_match = fuzz.ratio(cn, normalize(ex.get("company_name", ""))) > 85
        role_match = fuzz.partial_ratio(rt, normalize(ex.get("role_title", ""))) > 80
        if company_match and role_match:
            return True
    return False


def chunk_list(lst: list[Any], size: int) -> list[list[Any]]:
    """Split a list into sublists of at most `size` elements."""
    return [lst[i: i + size] for i in range(0, len(lst), size)]


def strip_md_fences(text: str) -> str:
    """
    Remove markdown code fences from a Gemini response.
    Should rarely be needed with google-genai SDK + response_schema,
    but kept as a fallback for gemini_text() calls.
    """
    text = re.sub(r"^```(?:json|markdown|md)?\s*", "", text.strip(), flags=re.IGNORECASE | re.MULTILINE)
    text = re.sub(r"\s*```$", "", text.strip(), flags=re.MULTILINE)
    return text.strip()


def recency_multiplier(posted_at: datetime | None) -> float:
    """
    Return a score multiplier (0.7–1.0) based on how recently the job was posted.
    Old listings are likely filled; penalise them in the ranking.

    Args:
        posted_at: Datetime of job posting (UTC), or None if unknown

    Returns:
        Float multiplier: 1.0 (fresh) → 0.7 (old/unknown)
    """
    if not posted_at:
        return 0.9  # Unknown date — slight penalty
    if posted_at.tzinfo is None:
        posted_at = posted_at.replace(tzinfo=timezone.utc)
    age_days = (datetime.now(timezone.utc) - posted_at).days
    if age_days <= 7:
        return 1.0
    elif age_days <= 21:
        return 0.95
    elif age_days <= 45:
        return 0.85
    else:
        return 0.7
