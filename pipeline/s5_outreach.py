"""
Stage 5: Personalised Outreach Drafts

Lazy-loaded — triggered on button click in Streamlit UI, not during pipeline.
One match_id processed at a time.

Flow per invocation:
  1. Fetch match (+ lead data) and candidate record from Supabase
  2. Cache check — return cached draft if outreach was already generated
  3. Tavily: quick company news lookup (1 call, 3 results, used for email context)
  4. Email enrichment: Apollo → Hunter fallback
     - If hiring_manager_name is null → skip enrichment entirely
  5. Gemini: single call returns outreach_hook + email + dm + personalisation_note
  6. Cache all fields in matches table, return OutreachDraft

Null hiring manager handling (common for formal LinkedIn Jobs / Wellfound listings):
  - Apollo/Hunter skipped (no name to look up)
  - Email is company-directed ("Dear [Company] Team")
  - dm becomes a 300-char LinkedIn connection request note template
  - personalisation_note instructs careers team to find the right contact
  - hiring_manager_identified=False → "Manual research needed" badge in UI
"""
import logging
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field
from dotenv import load_dotenv

from models.outreach import OutreachDraft, EmailDraft
from utils.gemini import gemini_json, MODEL_PRO
from utils.search import tavily_search
from utils.enrichment import find_email
from db.queries import get_match, get_candidate, update_match_outreach

load_dotenv()
log = logging.getLogger(__name__)


# ── Gemini output schema (internal — not returned directly to caller) ──────────

class _EmailOutput(BaseModel):
    subject: str = Field(
        description="6-8 word subject line. Direct. No clickbait. "
                    "Format: 'Introducing [First Name] for your [Role] opening'"
    )
    body: str = Field(
        description="Email body only — no greeting, no sign-off. "
                    "Follow the 4-part structure: Context → Hook → Proof (2 bullets) → CTA."
    )

class _OutreachGeminiResponse(BaseModel):
    outreach_hook:        str = Field(
        description="ONE specific sentence linking the candidate's unique background "
                    "to this company's exact need for this role. "
                    "Anchor it to the x_factor. NOT generic ('has diverse experience' = bad). "
                    "Good: 'Built and scaled a 0-to-1 ops function at a Series A — "
                    "exactly the muscle [Company] needs right now.'"
    )
    email:                _EmailOutput
    dm:                   str = Field(
        description="See prompt for which variant to generate (DM vs connection request)."
    )
    personalisation_note: str = Field(
        description="ONE specific action or verification the careers team must complete "
                    "before hitting send. If no hiring manager: must include how to find the right person."
    )


# ── Gemini system prompt (Mesa Placement Director persona) ────────────────────

_S5_SYSTEM = """\
You are the Placement Director at Mesa School of Business — India's most \
founder-focused business school, backed by Elevation Capital and taught by \
Harvard & Kellogg alumni. You introduce top students to hiring founders and managers.

TONE: Crisp. Operator-to-operator. Zero fluff. Short sentences.

FORBIDDEN PHRASES (never use any of these):
"I hope this email finds you well", "I wanted to reach out", "I came across your posting",
"leverage synergies", "passionate about", "unique opportunity", "excited to connect",
"I believe", "I feel", "touch base", "circle back".

EMAIL BODY STRUCTURE — follow this exact 4-part flow:
  (1) Context (1 sentence): Reference the specific role and how you saw it. \
If recent company news is provided, open with that hook instead.
  (2) Hook (1-2 sentences): Use the outreach_hook — link the candidate's specific \
background to this company's exact problem right now.
  (3) Proof (exactly 2 bullet points): Specific, quantified achievements from \
the candidate's history that directly map to this role's requirements.
  (4) CTA (1 sentence): "Happy to send over [First Name]'s full dossier — worth a look?"

Return valid JSON only. No markdown fences.\
"""


# ── Entry point ───────────────────────────────────────────────────────────────

def generate_outreach_on_demand(
    candidate_id: str,
    match_id: str,
) -> OutreachDraft:
    """
    Generate personalised outreach drafts for a specific match.
    Called on-demand from Streamlit UI — not during the main pipeline run.

    Returns cached OutreachDraft if already generated (re-click is instant).
    Otherwise runs the full pipeline and caches the result.

    Args:
        candidate_id: UUID of the candidate in Supabase
        match_id:     UUID of the match record (links candidate ↔ lead)

    Returns:
        OutreachDraft with email, dm, outreach_hook, personalisation_note
    """
    # ── Step 1: Fetch match + candidate data ─────────────────────────────────
    match     = get_match(match_id)
    candidate = get_candidate(candidate_id)

    if not match:
        raise ValueError(f"Match {match_id} not found in Supabase")
    if not candidate:
        raise ValueError(f"Candidate {candidate_id} not found in Supabase")

    log.info("S5: Generating outreach for %s @ %s (match %s)",
             match.get("role_title", "?"), match.get("company_name", "?"), match_id)

    # ── Step 2: Cache check ───────────────────────────────────────────────────
    if match.get("outreach_generated_at") and match.get("email_body"):
        log.info("S5: Returning cached outreach draft for match %s", match_id)
        return _build_draft_from_cache(match)

    # ── Step 3: Tavily — quick company news lookup ────────────────────────────
    company_name = match.get("company_name", "")
    news_snippet = _get_company_news(company_name)
    log.debug("S5: Company news for %s: '%s'", company_name, news_snippet[:80] if news_snippet else "none")

    # ── Step 4: Email enrichment (Apollo → Hunter) ────────────────────────────
    hiring_manager_name  = match.get("hiring_manager_name")
    has_manager          = bool(hiring_manager_name)
    hiring_manager_email = None

    if has_manager:
        log.info("S5: Looking up email for %s @ %s", hiring_manager_name, company_name)
        hiring_manager_email = find_email(
            name         = hiring_manager_name,
            company      = company_name,
            domain       = _infer_domain_from_url(match.get("post_url") or match.get("signal_url")),
            linkedin_url = match.get("hiring_manager_linkedin"),   # anchors Apollo to exact profile
        )
        log.info("S5: Email lookup — %s", "found" if hiring_manager_email else "not found")
    else:
        log.info("S5: No hiring manager name — skipping Apollo/Hunter, using company-directed output")

    # ── Step 5: Gemini — generate outreach_hook + email + dm + note ───────────
    gemini_response = _call_gemini_outreach(
        candidate    = candidate,
        match        = match,
        manager_name = hiring_manager_name,
        manager_email= hiring_manager_email,
        news_snippet = news_snippet,
        has_manager  = has_manager,
    )

    # ── Step 6: Cache in Supabase ─────────────────────────────────────────────
    update_match_outreach(match_id, {
        "outreach_hook":        gemini_response.outreach_hook,
        "email_subject":        gemini_response.email.subject,
        "email_body":           gemini_response.email.body,
        "dm_draft":             gemini_response.dm,
        "personalisation_note": gemini_response.personalisation_note,
        "hiring_manager_email": hiring_manager_email,
    })

    log.info("S5: Outreach generated and cached for match %s", match_id)

    return OutreachDraft(
        email                     = EmailDraft(
                                        subject=gemini_response.email.subject,
                                        body=gemini_response.email.body,
                                    ),
        dm                        = gemini_response.dm,
        personalisation_note      = gemini_response.personalisation_note,
        outreach_hook             = gemini_response.outreach_hook,
        hiring_manager_email      = hiring_manager_email,
        hiring_manager_identified = has_manager,
        email_found               = bool(hiring_manager_email),
    )


# ── Gemini call ───────────────────────────────────────────────────────────────

def _call_gemini_outreach(
    candidate:     dict,
    match:         dict,
    manager_name:  Optional[str],
    manager_email: Optional[str],
    news_snippet:  str,
    has_manager:   bool,
) -> _OutreachGeminiResponse:
    """
    Single Gemini call that generates outreach_hook + email + dm + personalisation_note.
    Prompt adapts based on whether a hiring manager is identified.
    """
    profile    = candidate.get("student_profile", {})
    x_factor   = candidate.get("x_factor", "")
    role_title = match.get("role_title", "this role")
    company    = match.get("company_name", "the company")
    rationale  = match.get("rationale", "")

    # Build candidate summary
    candidate_block = _format_candidate_block(profile, x_factor)

    # Email recipient line
    if has_manager:
        recipient_line = f"RECIPIENT: {manager_name} at {company}"
        email_note     = (
            f"Email and DM are both addressed to {manager_name} personally.\n"
            f"EMAIL FOUND: {'Yes — ' + manager_email if manager_email else 'No — write the email anyway, addressed to them by name, without referencing their email address.'}\n"
            "dm: Write a 3-sentence LinkedIn DM to this person. End with a yes/no question."
        )
    else:
        recipient_line = f"RECIPIENT: {company} team (no specific hiring manager identified)"
        email_note     = (
            "Email should be company-directed — no named recipient. "
            f"Start the body with: 'Noticed {company} is hiring for {role_title} — "
            "wanted to make an introduction.'\n"
            "dm: Write a LinkedIn CONNECTION REQUEST NOTE (not a DM). "
            "Max 300 characters. Must stand alone without knowing who will receive it. "
            "Do not reference the recipient by name."
        )

    # News line
    news_line = (
        f"RECENT COMPANY NEWS (use to open the email context sentence):\n{news_snippet}"
        if news_snippet else
        "RECENT COMPANY NEWS: None found — open with the role/company reference instead."
    )

    user_prompt = f"""\
{recipient_line}
ROLE: {role_title} at {company}
STAGE 4 FIT RATIONALE: {rationale}

{news_line}

{candidate_block}

INSTRUCTIONS:
{email_note}
personalisation_note: {
    f"Specific action to find the right contact at {company} before sending. "
    f"Include: 'Search {company!r} + {role_title!r} or CEO/founder on LinkedIn.'"
    if not has_manager else
    "One specific thing to verify before sending (e.g. confirm role is still open, "
    "check if the listing is recent)."
}
"""

    return gemini_json(
        prompt = user_prompt,
        system = _S5_SYSTEM,
        schema = _OutreachGeminiResponse,
        model  = MODEL_PRO,
    )


# ── Company news lookup ───────────────────────────────────────────────────────

def _get_company_news(company_name: str) -> str:
    """
    Quick Tavily search for recent company news.
    Returns a 1-2 sentence snippet from the top result, or empty string if nothing found.
    Used to ground the email opening sentence with something recent and specific.
    """
    if not company_name or company_name.lower() in ("unknown", ""):
        return ""
    try:
        results = tavily_search(
            f'"{company_name}" news announcement funding 2025 2026',
            max_results=3,
            days_back=180,
        )
        if not results:
            return ""
        # Use the highest-scoring result's content snippet
        best = max(results, key=lambda r: r.get("score", 0.0))
        snippet = best.get("content", "").strip()
        # Truncate to ~250 chars to keep prompt lean
        if len(snippet) > 250:
            snippet = snippet[:247].rstrip() + "..."
        return snippet
    except Exception as e:
        log.warning("S5: Company news lookup failed for '%s': %s", company_name, e)
        return ""


# ── Candidate block formatter ─────────────────────────────────────────────────

def _format_candidate_block(profile: dict, x_factor: str) -> str:
    """
    Format the candidate's profile data into a compact block for the Gemini prompt.
    Includes name, headline, x_factor, top 2 recent roles, and top 8 skills.
    """
    name     = profile.get("name", "the candidate")
    headline = profile.get("headline", "")
    skills   = profile.get("skills", [])[:8]
    history  = profile.get("role_history", [])[:2]

    roles_text = "\n".join(
        f"  - {r.get('title', '')} at {r.get('company', '')} ({r.get('duration', '')}): "
        f"{r.get('description', '')[:150]}"
        for r in history
    ) or "  No role history available"

    skills_text = ", ".join(skills) if skills else "Not specified"

    return (
        f"CANDIDATE TO INTRODUCE:\n"
        f"Name: {name}\n"
        f"Current: {headline}\n"
        f"x_factor: {x_factor}\n"
        f"Recent roles:\n{roles_text}\n"
        f"Top skills: {skills_text}"
    )


# ── Cache reconstruction ──────────────────────────────────────────────────────

def _build_draft_from_cache(match: dict) -> OutreachDraft:
    """
    Reconstruct an OutreachDraft from cached fields in the matches table.
    Called when outreach was already generated in a previous session.
    """
    return OutreachDraft(
        email = EmailDraft(
            subject = match.get("email_subject") or "",
            body    = match.get("email_body")    or "",
        ),
        dm                        = match.get("dm_draft")             or "",
        personalisation_note      = match.get("personalisation_note") or "",
        outreach_hook             = match.get("outreach_hook")        or "",
        hiring_manager_email      = match.get("hiring_manager_email"),
        hiring_manager_identified = bool(match.get("hiring_manager_name")),
        email_found               = bool(match.get("hiring_manager_email")),
    )


# ── Utility ───────────────────────────────────────────────────────────────────

def _infer_domain_from_url(url: Optional[str]) -> Optional[str]:
    """
    Extract company domain from a job post URL to help Hunter.io lookup.
    E.g. 'https://wellfound.com/jobs/123-chief-of-staff-at-acme' → not helpful.
        'https://careers.acme.com/jobs/cos' → 'acme.com'
    Returns None if domain can't be reliably inferred.
    """
    if not url:
        return None
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        host   = parsed.netloc.lower().replace("www.", "")
        # Skip known job board domains — they aren't the company's domain
        job_boards = {
            "linkedin.com", "wellfound.com", "angel.co",
            "indeed.com", "naukri.com", "glassdoor.com",
        }
        if any(board in host for board in job_boards):
            return None
        return host
    except Exception:
        return None
