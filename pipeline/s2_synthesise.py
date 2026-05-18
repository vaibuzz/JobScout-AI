"""
Stage 2: Candidate Synthesis

Takes the validated StudentProfile from Stage 1.
Uses Gemini to derive: target roles, sector fit, compensation band,
dealbreakers, x_factor, and pre-built Boolean search queries.

Compensation is grounded with a Tavily web search and buffered
+5L above market rate to ensure Mesa students aim above-average.
"""
import os
import logging

from dotenv import load_dotenv
from models.student import StudentProfile, CandidateModel
from utils.gemini import MODEL_PRO

load_dotenv()
log = logging.getLogger(__name__)

# Absolute minimum we ever show — a genuinely weak profile never drops below this.
# These are floors, not averages. Averages are higher; Tavily grounds the actual band.
FRESHER_BENCHMARK_LPA    = int(os.getenv("FRESHER_BENCHMARK_LPA", "18"))
NON_FRESHER_BENCHMARK_LPA = int(os.getenv("NON_FRESHER_BENCHMARK_LPA", "28"))

_S2_SYSTEM = """You are an elite executive recruiter specialising in Indian startups
and high-growth companies across ALL sectors -- tech, consumer, fintech, legal,
media, D2C, operations, and more. Analyse this candidate's profile and output
their optimal job market positioning.

======================================================================
STEP 0: READ INTENT BEFORE READING HISTORY (do this first, always)
======================================================================

Before analysing past roles, answer these two questions:

Q1 -- PIVOT CHECK: Has this candidate clearly changed career direction?
  Look at the trajectory. If the most recent 2+ years of experience are in
  a different domain than the earlier career, the earlier domain is background
  context ONLY. Do NOT target roles in a domain the candidate has clearly exited.
  The recency and duration of the change determines the weight -- a 3-year pivot
  outweighs a 10-year history in the old field.

Q2 -- MESA PGP SIGNAL: Does the education section show a Mesa specialisation?
  Check education for "Mesa School of Business". If a specific specialisation
  is listed (e.g. "Product Management", "Startup Leadership", "Finance"),
  treat this as a strong declared intent signal. Weight roles aligned with
  that specialisation MORE heavily than historical titles -- even if the
  candidate's past experience is in a different domain. The Mesa programme
  exists precisely to enable this transition.

======================================================================
STEP 1: SELECT TARGET ROLES (max 3)
======================================================================

Pick only the highest-confidence fits. Be specific with titles.

EVIDENCE GATE — apply before assigning any confidence level:
  For every role you consider, the profile must contain AT LEAST ONE of:
    a) A job title that IS this role or a direct predecessor (e.g. "Analyst" → "Senior Analyst")
    b) A specific, quantified achievement that IS this role's core responsibility
       (e.g. "built product roadmap", "ran A/B tests on funnel", "managed P&L")
    c) A Mesa PGP specialisation that explicitly names this function

  Confidence rules:
    HIGH   → evidence type (a) or (b) is clearly present and recent (last 3 years)
    MEDIUM → evidence exists but is indirect, old, or via a Mesa specialisation (c)
    LOW    → only vague evidence ("managed projects", "cross-functional collaboration")
    DROP   → no evidence of any kind — do not include this role at all

  NEVER assign HIGH confidence to a role the candidate has never held and has no
  direct deliverable for. Aspirational fits must be MEDIUM or LOW, not HIGH.

GENERALIST PROFILES (varied experience, no single deep domain):
  Do NOT pick 3 unrelated roles. Find the 2-3 roles that form ONE coherent
  story about this person. The roles must reinforce each other, not scatter.
  Ask yourself: "What single narrative connects all their experiences?"
  The roles should all point toward the SAME type of hiring manager,
  not three different people at three different companies.

CAREER CHANGER RULE:
  If the candidate has clearly left a profession, do NOT include roles from
  that old profession as target roles UNLESS the profile explicitly signals
  return interest (e.g. "looking to return to law"). Instead, add it to
  dealbreakers: "Traditional [old profession] roles with no business ownership".

For each target role, generate 2-3 aliases: real alternative titles that
hiring managers actually post for the same function.
  Good alias: a title that hiring managers actually type into the job posting form.
  Bad alias: team or department names (e.g. "Growth Team", "Founders Office",
  "Office of the CEO" -- these describe a team, not a job title that gets posted).
  Bad alias: anything vague or generic like "Leadership Role" or "Management Position".
  Every alias must pass the same 8-company market demand test as the primary title.

ROLE DIFFERENTIATION RULE:
  The 3 target roles must open genuinely different job pools in the market.
  Test: would two roles attract near-identical Apify search results and identical
  hiring managers? If yes, they overlap too much -- replace the weaker one with
  a role that reaches different companies or a different type of decision-maker.
  Each role slot must add unique search coverage, not duplicate another slot.

======================================================================
STEP 2: MARKET DEMAND CHECK (applies to every role, every domain)
======================================================================

Before finalising ANY role as a primary title, apply the NAME-THE-COMPANIES TEST:

  TEST: Based on your knowledge of India's job market (LinkedIn, Wellfound,
  Naukri), can you name at least 8 specific Indian companies actively posting
  this EXACT job title right now?

  If YES -> include as the primary role title.
  If NO  -> REPLACE with the closest broader title that passes the test.
            Move the niche/specific title to aliases instead.
            If no honest replacement exists, DROP the role entirely.

This test is domain-agnostic. It applies equally to tech, legal, ops,
marketing, finance, design, consulting, and any other domain.

The candidate's specialisation is preserved in aliases -- it is NOT lost.
The primary title simply needs to match what employers actually post.

======================================================================
STEP 3: FILL ALL OTHER FIELDS
======================================================================

- sector_fit: 3-6 industry verticals where this person would thrive.
  Use Step 0 pivot signals to lead with the INTENDED sectors, not just
  the historical ones.
  Only include sectors where the candidate has a realistic path to getting hired.
  If a sector requires specific background that is NOT evidenced in the profile,
  do not include it based on general business acumen alone. Every sector listed
  must have a clear, defensible connection to something in the candidate's history.

- compensation_band: SET TO NULL -- filled by a separate web search.

- dealbreakers: specific role/company types that are bad fits for this person.
  If the candidate has clearly exited a profession, include that old profession
  as a dealbreaker (e.g. "Traditional [old domain] roles with no business
  ownership" -- fill in the actual domain from the profile, do not use this
  template literally).

- x_factor: ONE specific sentence. Must be the candidate's sharpest
  differentiator. For multi-domain/generalist profiles, the x_factor must
  capture the INTERSECTION of their backgrounds as a single unfair advantage --
  not just list what they have done.
  Bad:  "Has experience across multiple industries and functions"
  Good: One sentence that names the SPECIFIC combination from THIS candidate's
        profile and explains WHY that combination creates an unfair advantage that
        a single-background candidate cannot replicate.

- search_queries.formal_platforms: primary role title strings for Apify
  LinkedIn Jobs + Wellfound scrapers. Use PRIMARY title only (NOT aliases).
  Max 3 strings total. (Legacy field — role_groups is authoritative.)

- search_queries.role_groups: THE PRIMARY SEARCH STRATEGY for LinkedIn Jobs.
  One entry per target role. Each entry must have:
    role_title:    The primary job title (e.g. 'Chief of Staff')
    confidence:    Same confidence as target_roles
    search_titles: [primary title] + all aliases from target_roles for this role.
                   E.g. for 'Chief of Staff' with aliases ['Founder\'s Office', 'Head of CEO Office']:
                   search_titles = ['Chief of Staff', "Founder's Office", 'Head of CEO Office']
                   Max 4 titles per group. First entry MUST be the primary title.
    hidden_signals: 1-2 '#Hiring ...' phrases specific to this role group.
                   Same rules as hidden_signals below. Max 2 per group.
  Generate exactly as many role_groups as target_roles. Max 3 groups.

- search_queries.hidden_signals: phrases for the LinkedIn Post Search Scraper.
  MUST start with "#Hiring" followed by the role title or a key alias.
  Keep to 2-4 words after #Hiring. Do NOT add any location suffix (no "India", no city).

  CRITICAL — use STARTUP-SPECIFIC titles only. Generic titles shared with all industries
  (e.g. "Growth Manager", "Sales Manager", "Marketing Manager", "Operations Manager")
  return irrelevant noise from banks, real estate, FMCG, and MNCs. Instead:
    - Prefer seniority-qualified or startup-native variants:
        "Growth Manager" → "#Hiring Head of Growth"
        "Marketing Manager" → "#Hiring Head of Marketing" or "#Hiring Growth Lead"
        "Operations Manager" → "#Hiring Chief of Staff" or "#Hiring Head of Operations"
        "Sales Manager" → "#Hiring Head of Sales" or "#Hiring VP Sales"
    - Titles that are inherently startup-specific can be used directly:
        "#Hiring Chief of Staff", "#Hiring Product Manager", "#Hiring Founding Engineer",
        "#Hiring VP Product", "#Hiring Head of Growth", "#Hiring General Manager"
  Max 5 strings total. Every string must reliably surface startup/founder posts,
  not generic industry hiring.

- confidence: 'high' if directly evidenced by past work, 'medium' if
  inferred or pivot-based, 'low' if a genuine stretch."""


def synthesise_candidate(candidate_id: str, student_profile: StudentProfile) -> CandidateModel:
    """
    Synthesise a CandidateModel from a validated StudentProfile.

    Args:
        candidate_id:   Supabase UUID of the candidate record
        student_profile: Validated StudentProfile from Stage 1

    Returns:
        CandidateModel with all positioning fields populated
    """
    from utils.gemini import gemini_json
    from db.queries import update_candidate_synthesis, update_candidate_status

    log.info("S2: Synthesising candidate %s (%s)", student_profile.name, candidate_id)

    # -- Step 1: Gemini synthesis (comp_band left null intentionally) -----------
    candidate_model = gemini_json(
        prompt   = f"CANDIDATE PROFILE:\n{student_profile.model_dump_json(indent=2)}",
        system   = _S2_SYSTEM,
        schema   = CandidateModel,
        model    = MODEL_PRO,
        thinking = True,
    )

    log.info(
        "S2: Synthesised -- %d target roles, x_factor: '%s...'",
        len(candidate_model.target_roles),
        candidate_model.x_factor[:60],
    )

    # -- Step 1b: Auto-build role_groups if Gemini didn't generate them ---------
    # Fallback ensures backward compat: if old prompt/DB data lacks role_groups,
    # we derive them deterministically from target_roles + aliases.
    if not candidate_model.search_queries.role_groups:
        from models.student import RoleGroup
        derived_groups = []
        for role in candidate_model.target_roles:
            search_titles = [role.title] + (role.aliases or [])  # primary first
            # Use hidden_signals that mention this role
            role_signals = [
                s for s in candidate_model.search_queries.hidden_signals
                if role.title.lower() in s.lower()
                or any(alias.lower() in s.lower() for alias in (role.aliases or []))
            ][:2]
            derived_groups.append(RoleGroup(
                role_title    = role.title,
                confidence    = role.confidence,
                search_titles = search_titles[:4],  # max 4 per group
                hidden_signals = role_signals,
            ))
        candidate_model.search_queries.role_groups = derived_groups
        log.info("S2: Auto-derived %d role_groups from target_roles", len(derived_groups))

    # -- Step 2: Ground compensation with Tavily web search --------------------
    comp_band = _ground_compensation(
        target_roles=[r.title for r in candidate_model.target_roles],
        is_fresher=student_profile.is_fresher,
    )
    candidate_model.compensation_band = comp_band

    log.info(
        "S2: Compensation grounded -- %dL-%dL (is_fresher=%s)",
        comp_band.low_lpa, comp_band.high_lpa, student_profile.is_fresher,
    )

    # -- Step 3: Persist to Supabase -------------------------------------------
    update_candidate_synthesis(candidate_id, {
        "target_roles":          [r.model_dump() for r in candidate_model.target_roles],
        "sector_fit":            candidate_model.sector_fit,
        "compensation_band_inr": f"{comp_band.low_lpa}L - {comp_band.high_lpa}L",
        "dealbreakers":          candidate_model.dealbreakers,
        "search_queries":        candidate_model.search_queries.model_dump(),
        "x_factor":              candidate_model.x_factor,
    })
    update_candidate_status(candidate_id, "synthesised")

    return candidate_model


def _ground_compensation(target_roles: list[str], is_fresher: bool):
    """
    Ground compensation in real market data via Tavily.

    Logic:
        absolute_floor  = 18L (fresher) or 28L (non-fresher) — never go below this
        market_lpa      = what Tavily finds companies actually paying for this role in India
        low  = max(absolute_floor, market_lpa - 2)   # near market but floored
        high = max(low + 4,        market_lpa + 5)   # Mesa premium: +5L above market

    A weak profile lands near the floor; a strong profile tracks market (or above).
    If Tavily returns nothing useful: (floor, floor + 6) — conservative default.
    """
    from utils.gemini import gemini_extract_salary_lpa
    from utils.search import tavily_search
    from models.student import CompensationBand

    absolute_floor = FRESHER_BENCHMARK_LPA if is_fresher else NON_FRESHER_BENCHMARK_LPA

    market_lpa = None
    if target_roles:
        role = target_roles[0]
        try:
            results = tavily_search(
                f'"{role}" salary India startup LPA lakhs per annum 2025 site:ambitionbox.com OR site:glassdoor.co.in OR site:levels.fyi',
                max_results=5,
                days_back=365,
            )
            combined_text = " ".join(r["content"] for r in results)
            market_lpa = gemini_extract_salary_lpa(combined_text)
        except Exception as e:
            log.warning("S2: Tavily comp grounding failed: %s — using floor", e)

    # Reject clearly wrong data (USD salaries / CXO-level pay picked up by Tavily)
    if market_lpa and market_lpa > 80:
        log.warning("S2: market_lpa=%d exceeds 80 LPA cap (likely USD/CXO data) — ignoring", market_lpa)
        market_lpa = None

    if market_lpa:
        low  = max(absolute_floor, market_lpa - 2)
        high = max(low + 4, market_lpa + 5)
    else:
        low  = absolute_floor
        high = absolute_floor + 6

    return CompensationBand(low_lpa=low, high_lpa=high)
