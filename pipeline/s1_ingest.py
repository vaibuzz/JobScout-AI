"""
Stage 1: Profile Ingestion & Normalisation

Two input paths (both produce identical StudentProfile output):
  A) LinkedIn URL → Apify LinkedIn Profile Scraper actor → raw JSON → Gemini normalise
  B) PDF upload  → pdfplumber text extract → Gemini normalise

Output: validated StudentProfile + candidate_id written to Supabase
"""
import os
import logging
import io
from pathlib import Path

from dotenv import load_dotenv
from models.student import StudentProfile

load_dotenv()
log = logging.getLogger(__name__)

APIFY_API_KEY = os.getenv("APIFY_API_KEY", "")

# Gemini system prompt for profile normalisation
_S1_SYSTEM = """You are a recruitment data engineer at Mesa School of Business.
Convert raw LinkedIn or resume data into a normalised student profile.

RULES:
- name, headline, seniority, is_fresher are REQUIRED — never leave blank
- skills: extract specific skills only (not "communication" — that's too vague), max 20
- domains: 3-5 industry domains this person operates in
- role_history: reverse chronological, most recent first
- seniority: use ONLY one of: junior, mid, senior, founder
- is_fresher: true if total work experience < 2 years
- preferences: ONLY include if clearly evidenced — do not invent
- location: 'City, Country' format or 'Not specified'
- For any missing field use empty string/list — do NOT hallucinate data"""


def ingest_profile(
    linkedin_url: str | None = None,
    pdf_bytes: bytes | None = None,
    student_name: str = "",
) -> tuple[StudentProfile, str]:
    """
    Ingest a student profile from LinkedIn URL or PDF bytes.

    Args:
        linkedin_url: LinkedIn profile URL (triggers Apify scrape)
        pdf_bytes:    PDF file as bytes (from Streamlit file_uploader)
        student_name: Optional name override

    Returns:
        (StudentProfile, candidate_id) — both written to Supabase
    """
    if not linkedin_url and not pdf_bytes:
        raise ValueError("Provide either linkedin_url or pdf_bytes")

    # ── Path A: LinkedIn URL via Apify ────────────────────────────────────────
    if linkedin_url and APIFY_API_KEY:
        log.info("S1: Ingesting via Apify Profile Scraper — %s", linkedin_url)
        raw_text = _apify_scrape_profile(linkedin_url)
        if raw_text:
            profile = _normalise_with_gemini(raw_text, input_type="url",
                                              linkedin_url=linkedin_url,
                                              name_hint=student_name)
            candidate_id = _save_to_db(profile)
            log.info("S1: Apify path complete — candidate_id=%s", candidate_id)
            return profile, candidate_id
        else:
            log.warning("S1: Apify scrape returned empty — falling back to PDF if provided")

    # ── Path B: PDF ────────────────────────────────────────────────────────────
    if pdf_bytes:
        log.info("S1: Ingesting via PDF upload")
        raw_text = _extract_pdf_text(pdf_bytes)
        profile = _normalise_with_gemini(raw_text, input_type="pdf",
                                          linkedin_url=linkedin_url,
                                          name_hint=student_name)
        candidate_id = _save_to_db(profile)
        log.info("S1: PDF path complete — candidate_id=%s", candidate_id)
        return profile, candidate_id

    raise RuntimeError(
        "S1 ingestion failed: Apify returned no data and no PDF was provided. "
        "Please upload a resume PDF."
    )


def _apify_scrape_profile(linkedin_url: str) -> str | None:
    """
    Call Apify LinkedIn Profile Scraper (actor LpVuK3Zozwuipa5bp) and return
    raw profile as a text block for Gemini.
    Actor: "LinkedIn Profile Scraper + Email ✅ No Cookies" — $4/1k profiles.
    Returns None if scrape fails or returns empty data.
    """
    try:
        from apify_client import ApifyClient
        client = ApifyClient(APIFY_API_KEY)

        run = client.actor("LpVuK3Zozwuipa5bp").call(
            run_input={
                "profileScraperMode": "Profile details no email ($4 per 1k)",
                "queries": [linkedin_url],
            }
        )

        items = list(client.dataset(run["defaultDatasetId"]).iterate_items())
        if not items:
            log.warning("S1: Apify returned 0 items for %s", linkedin_url)
            return None

        profile_data = items[0]
        if profile_data.get("status") not in (200, None) and profile_data.get("status") != "":
            # Some actor errors surface as status != 200
            log.warning("S1: Actor returned status=%s for %s", profile_data.get("status"), linkedin_url)

        return _apify_profile_to_text(profile_data)

    except Exception as e:
        log.error("S1: Apify scrape failed: %s", e)
        return None


def _apify_profile_to_text(data: dict) -> str:
    """
    Convert actor LpVuK3Zozwuipa5bp JSON output into a readable text block for Gemini.

    Key field differences from old actor:
      fullName      → firstName + lastName
      experiences   → experience  (list, each item: position / companyName / startDate.text / endDate.text / description)
      educations    → education   (list, each item: schoolName / degree / fieldOfStudy / startDate.text / endDate.text)
      jobLocation   → location.linkedinText
      skills[].title → skills[].name
    """
    lines = []

    # Name
    first = data.get("firstName", "")
    last  = data.get("lastName", "")
    full  = f"{first} {last}".strip() or data.get("fullName", "")
    lines.append(f"Name: {full}")

    # Headline & about
    lines.append(f"Headline: {data.get('headline', '')}")
    about = data.get("about", "")
    if about:
        lines.append(f"Summary: {about[:500]}")

    # Location — nested object: location.linkedinText
    loc = data.get("location", "")
    if isinstance(loc, dict):
        loc = loc.get("linkedinText") or loc.get("parsed", {}).get("text", "")
    lines.append(f"Location: {loc}")

    # Work experience — field is "experience" (singular), not "experiences"
    experiences = data.get("experience") or data.get("experiences", [])
    if experiences:
        lines.append("\nWork Experience:")
        for exp in experiences:
            title   = exp.get("position") or exp.get("title", "")
            company = exp.get("companyName", "")
            # startDate / endDate are nested objects: {"text": "Jan 2024", "month": "Jan", "year": 2024}
            start_obj = exp.get("startDate", {})
            end_obj   = exp.get("endDate", {})
            start = start_obj.get("text", "") if isinstance(start_obj, dict) else str(start_obj or "")
            end   = end_obj.get("text", "")   if isinstance(end_obj,   dict) else str(end_obj   or "")
            if not end or end.lower() == "none":
                end = "Present"
            duration = exp.get("duration", "")
            desc = (exp.get("description") or exp.get("jobDescription") or "")[:400]
            lines.append(f"  - {title} at {company} ({start} – {end}, {duration})")
            if desc:
                lines.append(f"    {desc}")

    # Education — field is "education" (singular), not "educations"
    educations = data.get("education") or data.get("educations", [])
    if educations:
        lines.append("\nEducation:")
        for edu in educations:
            school = edu.get("schoolName", "")
            degree = edu.get("degree") or edu.get("degreeName", "")
            field  = edu.get("fieldOfStudy", "") or ""
            start_obj = edu.get("startDate", {})
            end_obj   = edu.get("endDate",   {})
            start = start_obj.get("text", "") if isinstance(start_obj, dict) else ""
            end   = end_obj.get("text",   "") if isinstance(end_obj,   dict) else ""
            period = edu.get("period") or (f"{start} – {end}".strip(" –") if (start or end) else "")
            lines.append(f"  - {degree} in {field} at {school} ({period})")

    # Skills — each item is a dict with "name" key (not "title")
    skills = data.get("skills", [])
    if skills:
        skill_names = []
        for s in skills[:20]:
            if isinstance(s, dict):
                skill_names.append(s.get("name") or s.get("title") or "")
            else:
                skill_names.append(str(s))
        skill_names = [s for s in skill_names if s]
        if skill_names:
            lines.append(f"\nSkills: {', '.join(skill_names)}")

    # Top skills string (bonus signal for Gemini)
    top_skills = data.get("topSkills", "")
    if top_skills:
        lines.append(f"Top Skills: {top_skills}")

    return "\n".join(lines)


def _extract_pdf_text(pdf_bytes: bytes) -> str:
    """
    Extract text from PDF bytes using pdfplumber.
    Returns concatenated text from all pages.
    """
    import pdfplumber

    text_parts = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:
                text_parts.append(page_text)

    full_text = "\n".join(text_parts)
    log.debug("S1: Extracted %d chars from PDF (%d pages)", len(full_text), len(text_parts))
    return full_text


def _normalise_with_gemini(
    raw_text: str,
    input_type: str,
    linkedin_url: str | None = None,
    name_hint: str = "",
) -> StudentProfile:
    """
    Send raw profile text to Gemini and get back a validated StudentProfile.
    Uses google-genai native response_schema — no manual JSON parsing needed.
    """
    from utils.gemini import gemini_json

    hint = f"\nStudent name hint (if name unclear in text): {name_hint}" if name_hint else ""

    profile = gemini_json(
        prompt=f"RAW PROFILE DATA:\n{raw_text}{hint}",
        system=_S1_SYSTEM,
        schema=StudentProfile,
    )

    # Inject metadata that Gemini doesn't set
    profile.input_type = input_type
    profile.linkedin_url = linkedin_url

    log.info(
        "S1: Normalised profile — name=%s seniority=%s is_fresher=%s",
        profile.name, profile.seniority, profile.is_fresher,
    )
    return profile


def _save_to_db(profile: StudentProfile) -> str:
    """Persist the StudentProfile to Supabase and return candidate_id."""
    from db.queries import upsert_candidate
    candidate_id = upsert_candidate({
        "name":           profile.name,
        "linkedin_url":   profile.linkedin_url,
        "input_type":     profile.input_type,
        "student_profile": profile.model_dump(),
    })
    return candidate_id
