"""
Stage 6: Internal Dossier Generation

Produces a 1-page Markdown memo for the Mesa Careers Team about a specific lead.
Lazy-loaded — triggered on button click in Streamlit UI, not during the pipeline.
Rank-agnostic at the code level; UI defaults to showing the button for rank=1 only.

Flow:
  1. Fetch match (+ lead data) and candidate from Supabase
  2. Cache check — return cached DossierOutput if already generated
  3. 3 parallel Tavily searches (ThreadPoolExecutor):
       A: "{company}" funding stage investors India startup
       B: "{company}" news announcement 2025 2026
       C (adaptive):
          → hiring_manager known → "{manager}" {company} background founder linkedin
          → hiring_manager null  → "{company}" founders CEO leadership team India
  4. gemini_text() call → raw Markdown (5 required sections)
  5. strip_md_fences() → section validation → retry once if any section missing
  6. Inject Stage 5 outreach_hook as lead line of "Why Fits" Reason 1 (if available)
  7. Append footer (date + Tavily source URLs)
  8. update_match_dossier() → cache in Supabase
  9. Return DossierOutput

Separately (called from app.py on download button click):
  export_dossier_pdf(markdown_content) → bytes via weasyprint
"""
import re
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Optional

from models.outreach import DossierOutput
from utils.gemini import gemini_text, MODEL_PRO
from utils.search import tavily_search
from db.queries import get_match, get_candidate, update_match_dossier

log = logging.getLogger(__name__)

# Required section headers — used for validation
_REQUIRED_SECTIONS = [
    "## Company Snapshot",
    "## Role Context",
    "## Why",                   # Partial match — candidate name varies
    "## Likely Objections",
    "## Competitive Landscape",
]


# ── Gemini system prompt ──────────────────────────────────────────────────────

_S6_SYSTEM = """\
You are a strategic career advisor at Mesa School of Business writing a \
1-page internal memo for the Mesa Careers Team. They use this to brief \
themselves before making an introduction call to a hiring founder or manager.

OUTPUT FORMAT: Pure Markdown only. No preamble, no introduction, no trailing text \
outside the sections. Start directly with the first section header.

GROUNDING RULE: Every company fact — funding amount, stage, team size, investors, \
product metrics, revenue — MUST come from the research provided in the prompt. \
If research does not confirm a fact, write "Not confirmed publicly". Do NOT invent.

STRUCTURE — use EXACTLY these 5 headers in this exact order:

## Company Snapshot
Stage, funding round + amount (if confirmed), key investors, team size, HQ, \
core product. One crisp paragraph. Source: funding research only.

## Role Context
Why does this specific role exist at this company right now? \
What does success look like in the first 90 days? \
Infer from the job description + company growth stage. 2-3 sentences.

## Why [CANDIDATE NAME] Fits — 3 Specific Reasons
Three surgical reasons connecting the candidate's actual experience to this \
company's specific needs. If an outreach_hook is provided, use it as the \
OPENING LINE of Reason 1. The x_factor must be the lead theme of Reason 1. \
Each reason is 2 sentences max. Format: **Reason N:** ...

## Likely Objections + How to Counter
2-3 objections the hiring manager might raise. Format each as: \
**Objection:** [what they might say] **Counter:** [one sharp response]

## Competitive Landscape
Exactly 2 sentences: (1) name 2-3 main competitors this company is actively \
racing right now. (2) Why that competitive pressure makes filling THIS specific \
role urgent.\
"""


# ── Entry point ───────────────────────────────────────────────────────────────

def generate_dossier(candidate_id: str, match_id: str) -> DossierOutput:
    """
    Generate a 1-page internal dossier for a specific lead.
    Rank-agnostic — UI controls which matches show the "Generate Dossier" button.
    Returns cached DossierOutput instantly on re-click (no Gemini re-run).

    Args:
        candidate_id: UUID of the candidate in Supabase
        match_id:     UUID of the match record (links candidate ↔ lead)

    Returns:
        DossierOutput with markdown_content, metadata, and Tavily source URLs
    """
    # ── Step 1: Fetch data ────────────────────────────────────────────────────
    match     = get_match(match_id)
    candidate = get_candidate(candidate_id)

    if not match:
        raise ValueError(f"Match {match_id} not found in Supabase")
    if not candidate:
        raise ValueError(f"Candidate {candidate_id} not found in Supabase")

    company_name  = match.get("company_name", "Unknown Company")
    role_title    = match.get("role_title",   "Unknown Role")
    manager_name  = match.get("hiring_manager_name")
    profile       = candidate.get("student_profile", {})
    candidate_name = profile.get("name", "the candidate")

    log.info("S6: Generating dossier for %s @ %s (match %s)",
             role_title, company_name, match_id)

    # ── Step 2: Cache check ───────────────────────────────────────────────────
    if match.get("dossier_generated_at") and match.get("dossier_markdown"):
        log.info("S6: Returning cached dossier for match %s", match_id)
        return _build_dossier_from_cache(match, candidate_name, company_name, role_title)

    # ── Step 3: Parallel Tavily research ──────────────────────────────────────
    log.info("S6: Running 3 parallel Tavily searches for %s", company_name)
    research = _research_company(company_name, role_title, manager_name)

    # ── Step 4: Build prompt + call Gemini ────────────────────────────────────
    prompt     = _build_dossier_prompt(candidate, match, research, candidate_name)
    raw_output = gemini_text(prompt=prompt, system=_S6_SYSTEM, model=MODEL_PRO)

    # ── Step 5: Strip fences + validate sections ──────────────────────────────
    clean_markdown = strip_md_fences(raw_output)
    missing        = _validate_sections(clean_markdown)

    if missing:
        log.warning("S6: Missing sections after first attempt: %s — retrying", missing)
        retry_prompt = (
            f"{prompt}\n\n"
            f"IMPORTANT: Your previous response was missing these required sections: "
            f"{', '.join(missing)}. You MUST include ALL 5 sections with their exact headers."
        )
        raw_retry      = gemini_text(prompt=retry_prompt, system=_S6_SYSTEM, model=MODEL_PRO)
        clean_markdown = strip_md_fences(raw_retry)
        still_missing  = _validate_sections(clean_markdown)
        if still_missing:
            log.warning("S6: Still missing after retry: %s — caching anyway", still_missing)

    # ── Step 6: Collect source URLs for footer ────────────────────────────────
    all_sources: list[str] = []
    for results in research.values():
        for r in results:
            url = r.get("url", "")
            if url and url not in all_sources:
                all_sources.append(url)
    unique_sources = all_sources[:8]   # Cap at 8 source URLs

    # ── Step 7: Append footer ─────────────────────────────────────────────────
    generated_at   = datetime.now(timezone.utc)
    final_markdown = _append_footer(clean_markdown, unique_sources, generated_at)

    # ── Step 8: Persist to Supabase ───────────────────────────────────────────
    update_match_dossier(match_id, final_markdown)
    log.info("S6: Dossier generated and cached for match %s (%d chars)",
             match_id, len(final_markdown))

    # ── Step 9: Return DossierOutput ─────────────────────────────────────────
    return DossierOutput(
        markdown_content = final_markdown,
        company_name     = company_name,
        role_title       = role_title,
        candidate_name   = candidate_name,
        generated_at     = generated_at,
        tavily_sources   = unique_sources,
    )


# ── Tavily research ───────────────────────────────────────────────────────────

def _research_company(
    company: str,
    role: str,
    manager_name: Optional[str],
) -> dict[str, list[dict]]:
    """
    Run 3 Tavily searches in parallel via ThreadPoolExecutor.
    Third search is adaptive: manager background if known, leadership team if not.
    Returns dict with keys 'funding', 'news', 'manager'.
    """
    searches = {
        "funding": f'"{company}" funding stage investors India startup',
        "news":    f'"{company}" news announcement 2025 2026',
        "manager": (
            f'"{manager_name}" {company} background founder linkedin'
            if manager_name
            else f'"{company}" founders CEO leadership team India'
        ),
    }

    results: dict[str, list[dict]] = {}

    with ThreadPoolExecutor(max_workers=3) as executor:
        future_map = {
            executor.submit(tavily_search, query, 4, 365): key
            for key, query in searches.items()
        }
        for future in as_completed(future_map):
            key = future_map[future]
            try:
                results[key] = future.result()
                log.debug("S6: Tavily '%s' → %d results", key, len(results[key]))
            except Exception as e:
                log.warning("S6: Tavily search '%s' failed: %s", key, e)
                results[key] = []

    return results


# ── Prompt builder ────────────────────────────────────────────────────────────

def _build_dossier_prompt(
    candidate:      dict,
    match:          dict,
    research:       dict,
    candidate_name: str,
) -> str:
    """
    Assemble the full user prompt injected into the Gemini call.
    Includes candidate profile, job data, Stage 4 analysis, and all Tavily research.
    Injects outreach_hook (Stage 5) as a hint for the 'Why Fits' section if available.
    """
    profile    = candidate.get("student_profile", {})
    x_factor   = candidate.get("x_factor", "")
    history    = profile.get("role_history", [])[:2]
    skills     = ", ".join(profile.get("skills", [])[:8]) or "Not specified"

    roles_text = "\n".join(
        f"  - {r.get('title','')} at {r.get('company','')} ({r.get('duration','')}):"
        f" {r.get('description','')[:150]}"
        for r in history
    ) or "  No role history available"

    # Job + match fields
    company       = match.get("company_name", "Unknown Company")
    role          = match.get("role_title",   "Unknown Role")
    description   = (match.get("description") or match.get("snippet") or "")[:600]
    stage_label   = match.get("company_stage_label") or "unknown"
    manager_name  = match.get("hiring_manager_name")  or "Not identified"
    manager_li    = match.get("hiring_manager_linkedin") or "Not available"
    final_score   = match.get("final_score", "N/A")
    rationale     = match.get("rationale", "Not available")

    # Stage 5 outreach_hook — inject if already generated, otherwise omit
    outreach_hook = match.get("outreach_hook")
    hook_line     = (
        f"\nOUTREACH HOOK (use as the opening line of Reason 1 in the Why Fits section):\n"
        f"{outreach_hook}\n"
        if outreach_hook else ""
    )

    # Format Tavily research blocks
    funding_block = _format_research_block(research.get("funding", []), "FUNDING / STAGE RESEARCH")
    news_block    = _format_research_block(research.get("news",    []), "RECENT COMPANY NEWS")
    manager_block = _format_research_block(
        research.get("manager", []),
        "HIRING MANAGER BACKGROUND" if match.get("hiring_manager_name") else "COMPANY LEADERSHIP",
    )

    return f"""\
CANDIDATE: {candidate_name}
x_factor: {x_factor}{hook_line}
Recent roles:
{roles_text}
Top skills: {skills}

JOB: {role} at {company}
Company stage: {stage_label}
Job description:
{description}

HIRING MANAGER: {manager_name}
LinkedIn: {manager_li}

STAGE 4 ANALYSIS:
Fit score: {final_score}/100
One-line rationale: {rationale}

{funding_block}

{news_block}

{manager_block}

Write the 1-page internal dossier using the 5-section structure. \
Only state facts confirmed by the research above. \
Write "Not confirmed publicly" for anything not in the research.\
"""


def _format_research_block(results: list[dict], label: str) -> str:
    """Format a list of Tavily results into a labelled research block for the prompt."""
    if not results:
        return f"== {label} ==\nNo data found."
    lines = []
    for r in results[:4]:
        url     = r.get("url", "unknown")
        content = r.get("content", "").strip()[:400]
        lines.append(f"Source: {url}\n{content}")
    return f"== {label} ==\n" + "\n\n".join(lines)


# ── Post-processing helpers ───────────────────────────────────────────────────

def strip_md_fences(text: str) -> str:
    """
    Remove markdown code fences that Gemini sometimes wraps around output
    even when explicitly told not to.  E.g. ```markdown ... ```
    """
    text = re.sub(r"^```(?:markdown|md)?\s*", "", text.strip(), flags=re.MULTILINE)
    text = re.sub(r"\s*```$",                  "", text.strip(), flags=re.MULTILINE)
    return text.strip()


def _validate_sections(markdown: str) -> list[str]:
    """
    Return a list of required section headers that are missing from the markdown.
    Uses case-insensitive partial matching to handle candidate-name variations.
    """
    lower   = markdown.lower()
    missing = [s for s in _REQUIRED_SECTIONS if s.lower() not in lower]
    return missing


def _append_footer(markdown: str, sources: list[str], generated_at: datetime) -> str:
    """Append a Mesa-branded footer with generation date and Tavily source URLs."""
    date_str     = generated_at.strftime("%d %B %Y")
    sources_text = "  ".join(
        f"[{i + 1}]({url})" for i, url in enumerate(sources) if url
    )
    footer = f"\n\n---\n*Prepared by Mesa Careers Team · {date_str}*"
    if sources_text:
        footer += f"\n*Sources: {sources_text}*"
    return markdown + footer


# ── Cache reconstruction ──────────────────────────────────────────────────────

def _build_dossier_from_cache(
    match:          dict,
    candidate_name: str,
    company_name:   str,
    role_title:     str,
) -> DossierOutput:
    """Reconstruct a DossierOutput from cached DB fields on re-click."""
    return DossierOutput(
        markdown_content = match.get("dossier_markdown", ""),
        company_name     = company_name,
        role_title       = role_title,
        candidate_name   = candidate_name,
        tavily_sources   = [],   # Not re-stored separately — embedded in markdown footer
    )


# ── PDF export (called from app.py on download button click) ──────────────────

def export_dossier_pdf(markdown_content: str) -> bytes:
    """
    Convert the dossier Markdown to a styled PDF.
    Called from app.py st.download_button() — does NOT re-call Gemini.

    Requires: pip install weasyprint markdown

    Returns:
        PDF file as bytes, ready for st.download_button()
    """
    try:
        import markdown as md_lib
        import weasyprint
    except ImportError as e:
        raise RuntimeError(
            "PDF export requires 'weasyprint' and 'markdown'. "
            f"Install with: pip install weasyprint markdown. Error: {e}"
        )

    html_body = md_lib.markdown(
        markdown_content,
        extensions=["tables", "fenced_code", "nl2br"],
    )

    styled_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <style>
    body       {{ font-family: Arial, sans-serif; margin: 44px 48px;
                  font-size: 13px; line-height: 1.65; color: #1a1a1a; }}
    h1, h2, h3 {{ color: #0f1f3d; }}
    h2         {{ font-size: 15px; border-bottom: 1px solid #dde3ec;
                  padding-bottom: 3px; margin-top: 22px; }}
    ul, ol     {{ margin-left: 18px; }}
    li         {{ margin-bottom: 3px; }}
    strong     {{ color: #0f1f3d; }}
    hr         {{ border: none; border-top: 1px solid #dde3ec; margin: 18px 0; }}
    em         {{ color: #555; font-size: 11px; }}
    a          {{ color: #2563eb; text-decoration: none; }}
    p          {{ margin: 6px 0; }}
  </style>
</head>
<body>
{html_body}
</body>
</html>"""

    return weasyprint.HTML(string=styled_html).write_pdf()
