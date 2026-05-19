"""
All DB read/write functions for Mesa Careers AI.

Table layout (v2 schema):
  candidates      — Stage 1 + Stage 2 output, one row per pipeline run
  direct_leads    — Structured Apify LinkedIn Jobs + Wellfound data
  indirect_leads  — Unstructured Apify LinkedIn Post Scraper signals
  matches         — Two-phase scored leads (initial pre-filter + final Gemini rank)

Two-phase scoring convention:
  initial_score   — Python math (direct) or Gemini batch-of-5 (indirect). For quota only.
  final_score     — Full Gemini scoring for all 20 quota-selected leads. Determines rank.
"""
import logging
from datetime import datetime, timezone, timedelta
from .client import get_client

log = logging.getLogger(__name__)


# ── Candidates ────────────────────────────────────────────────────────────────

def upsert_candidate(profile_data: dict) -> str:
    """
    Insert a new candidate row. Returns the new candidate_id (UUID).

    NOTE: 30-day URL deduplication cache was removed — it was a holdover from
    when Proxycurl API credits were being conserved. We no longer use Proxycurl,
    so every new submission must create a fresh candidate row with its own
    pipeline run, target roles, and scored matches.
    """
    db = get_client()
    linkedin_url = profile_data.get("linkedin_url")

    result = (
        db.table("candidates")
        .insert({
            "name":            profile_data.get("name", "Unknown"),
            "linkedin_url":    linkedin_url,
            "input_type":      profile_data.get("input_type", "unknown"),
            "student_profile": profile_data.get("student_profile", {}),
            "pipeline_status": "ingested",
        })
        .execute()
    )
    candidate_id = result.data[0]["id"]
    log.info("upsert_candidate: created id=%s", candidate_id)
    return candidate_id


def update_candidate_status(candidate_id: str, status: str, error: str | None = None):
    """Update pipeline_status (and optionally error_message) for a candidate."""
    db = get_client()
    payload: dict = {"pipeline_status": status, "updated_at": "now()"}
    if error:
        payload["error_message"] = error
    db.table("candidates").update(payload).eq("id", candidate_id).execute()


def update_candidate_synthesis(candidate_id: str, synthesis: dict):
    """Write Stage 2 Gemini synthesis results to the candidates table."""
    db = get_client()
    db.table("candidates").update({
        "target_roles":          synthesis.get("target_roles"),
        "sector_fit":            synthesis.get("sector_fit"),
        "compensation_band_inr": synthesis.get("compensation_band_inr"),
        "dealbreakers":          synthesis.get("dealbreakers"),
        "search_queries":        synthesis.get("search_queries"),
        "x_factor":              synthesis.get("x_factor"),
        "pipeline_status":       "synthesised",
        "updated_at":            "now()",
    }).eq("id", candidate_id).execute()


def get_candidate(candidate_id: str) -> dict | None:
    """Fetch a full candidate record by UUID."""
    db = get_client()
    result = db.table("candidates").select("*").eq("id", candidate_id).limit(1).execute()
    return result.data[0] if result.data else None


def get_recent_candidates(limit: int = 20) -> list[dict]:
    """
    Fetch recent pipeline runs for the History view, newest first.
    Returns lightweight candidate records (no student_profile blob).
    """
    db = get_client()
    result = (
        db.table("candidates")
        .select(
            "id, name, created_at, pipeline_status, input_type, "
            "target_roles, compensation_band_inr, x_factor"
        )
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return result.data or []


# ── Direct Leads ──────────────────────────────────────────────────────────────

_VALID_STAGE_LABELS = frozenset({
    'pre_seed', 'seed', 'series_a', 'series_b', 'series_c',
    'late_stage', 'public', 'mnc', 'unknown',
})

def _safe_stage(label) -> str | None:
    """Normalise a company_stage_label to match the DB CHECK constraint. Returns None if blank."""
    if not label:
        return None
    norm = str(label).lower().strip().replace(" ", "_").replace("-", "_")
    # Handle common variants
    aliases = {
        'seriesa': 'series_a', 'seriesb': 'series_b', 'seriesc': 'series_c',
        'preseed': 'pre_seed', 'pre-seed': 'pre_seed',
        'latestage': 'late_stage', 'late-stage': 'late_stage',
        'growth': 'late_stage', 'ipo': 'public',
    }
    norm = aliases.get(norm, norm)
    return norm if norm in _VALID_STAGE_LABELS else 'unknown'


def upsert_direct_lead(lead: dict) -> str:
    """
    Insert or update a structured job listing (LinkedIn Jobs or Wellfound).
    Dedup key: (company_name, role_title, source_platform).
    Returns the direct_lead_id UUID.
    """
    db = get_client()
    result = (
        db.table("direct_leads")
        .upsert(
            {
                "company_name":            lead.get("company_name", ""),
                "role_title":              lead.get("role_title", ""),
                "description":             lead.get("description"),
                "key_requirements":        lead.get("key_requirements", []),
                "location":                lead.get("location"),
                "remote_ok":               lead.get("remote_ok", False),
                "salary_estimate":         lead.get("salary_estimate") or lead.get("compensation_estimate"),
                "salary_lpa_parsed":       lead.get("salary_lpa_parsed"),
                "source_platform":         lead.get("source_platform", "LinkedIn Jobs"),
                "post_url":                lead.get("post_url"),
                # posted_at is only set for LinkedIn Jobs; Wellfound rows leave it NULL
                "posted_at":               lead.get("posted_at"),
                "apify_job_id":            lead.get("apify_job_id"),
                "company_stage_label":     _safe_stage(lead.get("company_stage_label")),
                "hiring_manager_name":     lead.get("hiring_manager_name"),
                "hiring_manager_linkedin": lead.get("hiring_manager_linkedin"),
            },
            on_conflict="company_name,role_title,source_platform",
        )
        .execute()
    )
    lead_id = result.data[0]["id"]
    log.debug("upsert_direct_lead: id=%s [%s @ %s]",
              lead_id, lead.get("role_title"), lead.get("company_name"))
    return lead_id


def get_direct_lead(lead_id: str) -> dict | None:
    """Fetch a single direct_lead record by UUID."""
    db = get_client()
    result = db.table("direct_leads").select("*").eq("id", lead_id).limit(1).execute()
    return result.data[0] if result.data else None


def get_direct_leads_for_candidate(candidate_id: str) -> list[dict]:
    """
    Fetch all direct leads linked to a candidate via their matches.
    Returns the lead rows (not the match rows).
    """
    db = get_client()
    result = (
        db.table("matches")
        .select("direct_leads(*)")
        .eq("candidate_id", candidate_id)
        .eq("source_type", "direct")
        .execute()
    )
    return [row["direct_leads"] for row in result.data if row.get("direct_leads")]


def get_recent_direct_leads_by_role(role_title: str, hours: int = 48) -> list[dict]:
    """
    Fetch direct leads scraped recently for a specific role title.
    Used to cache discovery and avoid redundant Apify API calls.
    """
    db = get_client()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    result = (
        db.table("direct_leads")
        .select("*")
        .eq("role_title", role_title)
        .gte("created_at", cutoff.isoformat())
        .execute()
    )
    return result.data or []


# ── Indirect Leads ────────────────────────────────────────────────────────────

def upsert_indirect_lead(lead: dict) -> str:
    """
    Insert or update an indirect signal (LinkedIn post from Apify Post Scraper).
    Dedup key: signal_url (each LinkedIn post URL is globally unique).
    Returns the indirect_lead_id UUID.
    """
    db = get_client()
    result = (
        db.table("indirect_leads")
        .upsert(
            {
                "company_name":            lead.get("company_name", "Unknown"),
                "role_title":              lead.get("role_title", "Unknown Role"),
                "snippet":                 lead.get("snippet", lead.get("description", ""))[:2000],
                "signal_url":              lead.get("signal_url", lead.get("post_url", "")),
                "platform":                lead.get("platform", "LinkedIn Post"),
                "posted_at":               lead.get("posted_at"),
                "hiring_manager_name":     lead.get("hiring_manager_name"),
                "hiring_manager_linkedin": lead.get("hiring_manager_linkedin"),
                "engagement_score":        lead.get("engagement_score"),
                "role_inferred":           lead.get("role_inferred", False),
                "extraction_confidence":   lead.get("extraction_confidence", "medium"),
            },
            on_conflict="signal_url",
        )
        .execute()
    )
    lead_id = result.data[0]["id"]
    log.debug("upsert_indirect_lead: id=%s [%s @ %s]",
              lead_id, lead.get("role_title"), lead.get("company_name"))
    return lead_id


def get_indirect_lead(lead_id: str) -> dict | None:
    """Fetch a single indirect_lead record by UUID."""
    db = get_client()
    result = db.table("indirect_leads").select("*").eq("id", lead_id).limit(1).execute()
    return result.data[0] if result.data else None


# ── Matches — Creation (Stage 3 calls these after persisting leads) ───────────

def create_match_direct(candidate_id: str, direct_lead_id: str) -> str:
    """
    Create a placeholder match record for a direct lead.
    initial_score and final_score are populated later by Stage 4.
    Returns match_id UUID.
    """
    db = get_client()
    result = (
        db.table("matches")
        .upsert(
            {
                "candidate_id":    candidate_id,
                "direct_lead_id":  direct_lead_id,
                "source_type":     "direct",
            },
            on_conflict="candidate_id,direct_lead_id",  # handled by partial index
        )
        .execute()
    )
    return result.data[0]["id"]


def create_match_indirect(candidate_id: str, indirect_lead_id: str) -> str:
    """
    Create a placeholder match record for an indirect lead.
    Returns match_id UUID.
    """
    db = get_client()
    result = (
        db.table("matches")
        .upsert(
            {
                "candidate_id":      candidate_id,
                "indirect_lead_id":  indirect_lead_id,
                "source_type":       "indirect",
            },
            on_conflict="candidate_id,indirect_lead_id",
        )
        .execute()
    )
    return result.data[0]["id"]


# ── Matches — Stage 4 Scoring Updates ────────────────────────────────────────

def update_match_initial_score(match_id: str, score: float, breakdown: dict):
    """
    Write Phase 1 initial score for a match.
    Called by Stage 4 after Python scoring (direct) or Gemini batch scoring (indirect).
    """
    db = get_client()
    db.table("matches").update({
        "initial_score":           round(score, 2),
        "initial_score_breakdown": breakdown,
    }).eq("id", match_id).execute()


def mark_matches_quota_selected(match_ids: list[str]):
    """
    Flag the top 20 quota-selected matches before Phase 2 final Gemini scoring.
    All other matches remain quota_selected=false.
    """
    if not match_ids:
        return
    db = get_client()
    db.table("matches").update({"quota_selected": True}).in_("id", match_ids).execute()
    log.debug("Marked %d matches as quota_selected", len(match_ids))


def update_match_final_score(
    match_id:          str,
    final_score:       float,
    axis_scores:       dict,
    rank:              int,
    rationale:         str,
    below_benchmark:   bool = False,
):
    """
    Write Phase 2 final Gemini score for a quota-selected match.
    final_score is what determines the displayed rank — not initial_score.
    """
    db = get_client()
    db.table("matches").update({
        "final_score":          round(final_score, 2),
        "axis_scores":          axis_scores,
        "rank":                 rank,
        "rationale":            rationale,
        "below_mesa_benchmark": below_benchmark,
    }).eq("id", match_id).execute()


# ── Matches — Reads ───────────────────────────────────────────────────────────

def get_leads_for_ranking(candidate_id: str) -> tuple[list[dict], list[dict]]:
    """
    Fetch all match records for Stage 4 initial scoring.
    Returns (direct_matches, indirect_matches) — each entry flattened with its lead data.
    Called BEFORE quota selection or final scoring, so no score filters are applied.
    """
    db = get_client()
    result = (
        db.table("matches")
        .select("*, direct_leads(*), indirect_leads(*)")
        .eq("candidate_id", candidate_id)
        .execute()
    )
    rows      = result.data or []
    flattened = [_flatten_match(row) for row in rows]
    direct    = [m for m in flattened if m.get("source_type") == "direct"]
    indirect  = [m for m in flattened if m.get("source_type") == "indirect"]
    return direct, indirect


def get_matches_for_candidate(candidate_id: str) -> list[dict]:
    """
    Fetch all scored matches for a candidate, sorted by final_score descending.
    Returns a list of flattened dicts — lead fields are merged into the match dict
    at the top level so callers don't need to handle nested objects.

    Only returns quota-selected matches that have been through Phase 2 scoring.
    """
    db = get_client()
    result = (
        db.table("matches")
        .select("*, direct_leads(*), indirect_leads(*)")
        .eq("candidate_id", candidate_id)
        .eq("quota_selected", True)
        .not_.is_("final_score", "null")
        .order("final_score", desc=True)
        .execute()
    )
    return [_flatten_match(row) for row in (result.data or [])]


def get_match(match_id: str) -> dict | None:
    """
    Fetch a single match record with joined lead data.
    Returns a flattened dict (same format as get_matches_for_candidate).
    """
    db = get_client()
    result = (
        db.table("matches")
        .select("*, direct_leads(*), indirect_leads(*)")
        .eq("id", match_id)
        .limit(1)
        .execute()
    )
    if not result.data:
        return None
    return _flatten_match(result.data[0])


def _flatten_match(row: dict) -> dict:
    """
    Merge the nested direct_leads or indirect_leads dict into the match row.
    Removes the nested keys and lifts lead fields to the top level.
    Adds a 'lead_id' key pointing to whichever lead was the source.
    """
    match = {k: v for k, v in row.items() if k not in ("direct_leads", "indirect_leads")}

    lead_data: dict = {}
    if row.get("direct_leads"):
        lead_data = row["direct_leads"]
        match["lead_id"] = lead_data.get("id")
    elif row.get("indirect_leads"):
        lead_data = row["indirect_leads"]
        match["lead_id"] = lead_data.get("id")

    # Merge lead fields — match-level keys take priority on collision
    for k, v in lead_data.items():
        if k not in match:
            match[k] = v

    return match


# ── Matches — Stage 5 Outreach (lazy) ────────────────────────────────────────

def update_match_outreach(match_id: str, outreach_data: dict):
    """
    Write Stage 5 outreach results to the match record.
    outreach_hook is also written here (generated in Stage 5, not Stage 4).
    """
    db = get_client()
    db.table("matches").update({
        "outreach_hook":          outreach_data.get("outreach_hook"),
        "email_subject":          outreach_data.get("email_subject"),
        "email_body":             outreach_data.get("email_body"),
        "dm_draft":               outreach_data.get("dm_draft"),
        "personalisation_note":   outreach_data.get("personalisation_note"),
        "hiring_manager_email":   outreach_data.get("hiring_manager_email"),
        "outreach_generated_at":  "now()",
    }).eq("id", match_id).execute()


# ── Matches — Stage 6 Dossier (lazy) ─────────────────────────────────────────

def update_match_dossier(match_id: str, markdown: str):
    """Write Stage 6 dossier markdown to the match record."""
    db = get_client()
    db.table("matches").update({
        "dossier_markdown":     markdown,
        "dossier_generated_at": "now()",
    }).eq("id", match_id).execute()


# ── Bulk Inserts ──────────────────────────────────────────────────────────────

def upsert_direct_leads_bulk(leads: list[dict]) -> list[str]:
    if not leads: return []
    db = get_client()
    payload = []
    for lead in leads:
        payload.append({
            "company_name":            lead.get("company_name", "Unknown"),
            "role_title":              lead.get("role_title", "Unknown Role"),
            "description":             lead.get("description", "")[:2000],
            "key_requirements":        lead.get("key_requirements", []),
            "location":                lead.get("location", "India"),
            "remote_ok":               lead.get("remote_ok", False),
            "salary_estimate":         lead.get("salary_estimate") or lead.get("compensation_estimate"),
            "salary_lpa_parsed":       lead.get("salary_lpa_parsed"),
            "source_platform":         lead.get("source_platform", "LinkedIn Jobs"),
            "post_url":                lead.get("post_url"),
            "posted_at":               lead.get("posted_at"),
            "apify_job_id":            lead.get("apify_job_id"),
            "company_stage_label":     _safe_stage(lead.get("company_stage_label")),
            "hiring_manager_name":     lead.get("hiring_manager_name"),
            "hiring_manager_linkedin": lead.get("hiring_manager_linkedin"),
        })
    
    # Dedup by the unique constraint: company_name + role_title + source_platform
    unique_payload = {}
    for p in payload:
        k = (p["company_name"], p["role_title"], p["source_platform"])
        unique_payload[k] = p
        
    result = db.table("direct_leads").upsert(
        list(unique_payload.values()), on_conflict="company_name,role_title,source_platform"
    ).execute()
    return [row["id"] for row in result.data]


def upsert_indirect_leads_bulk(leads: list[dict]) -> list[str]:
    if not leads: return []
    db = get_client()
    payload = []
    for lead in leads:
        payload.append({
            "company_name":            lead.get("company_name", "Unknown"),
            "role_title":              lead.get("role_title", "Unknown Role"),
            "snippet":                 lead.get("snippet", lead.get("description", ""))[:2000],
            "signal_url":              lead.get("signal_url", lead.get("post_url", "")),
            "platform":                lead.get("platform", "LinkedIn Post"),
            "posted_at":               lead.get("posted_at"),
            "hiring_manager_name":     lead.get("hiring_manager_name"),
            "hiring_manager_linkedin": lead.get("hiring_manager_linkedin"),
            "engagement_score":        lead.get("engagement_score"),
            "role_inferred":           lead.get("role_inferred", False),
            "extraction_confidence":   lead.get("extraction_confidence", "medium"),
        })
        
    # Dedup by signal_url
    unique_payload = {p["signal_url"]: p for p in payload if p["signal_url"]}
    if not unique_payload: return []
    
    result = db.table("indirect_leads").upsert(
        list(unique_payload.values()), on_conflict="signal_url"
    ).execute()
    return [row["id"] for row in result.data]


def create_matches_direct_bulk(candidate_id: str, lead_ids: list[str]):
    if not lead_ids: return
    db = get_client()
    payload = [
        {"candidate_id": candidate_id, "direct_lead_id": lid, "source_type": "direct"}
        for lid in lead_ids
    ]
    db.table("matches").upsert(
        payload, on_conflict="candidate_id,direct_lead_id"
    ).execute()


def create_matches_indirect_bulk(candidate_id: str, lead_ids: list[str]):
    if not lead_ids: return
    db = get_client()
    payload = [
        {"candidate_id": candidate_id, "indirect_lead_id": lid, "source_type": "indirect"}
        for lid in lead_ids
    ]
    db.table("matches").upsert(
        payload, on_conflict="candidate_id,indirect_lead_id"
    ).execute()

