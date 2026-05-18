"""
Stage 4: Scoring & Ranking Engine

Two-phase pipeline that reduces ~50 raw leads to a final ranked top-20.

Phase 1 — Initial scoring (fast pre-filter, selects top 20 for Phase 2):
  1A: Direct leads  → Python math: stage score + recency + alias confidence weight
  1B: Indirect leads → Gemini batch-of-5: role intent + culture fit (no salary/location penalty)

Quota selection: top 12 direct + top 8 indirect = 20 leads.
  Edge case: if indirect < 8, expand direct quota to fill 20.

Phase 2 — Final Gemini scoring (all 20 quota-selected leads):
  Both direct and indirect get the identical prompt.
  Python computes final_score from Gemini axis scores + stage multiplier.
  final_score is the only ranking signal — no source distinction.

Output: list[ScoredLead] sorted by final_score descending, persisted to matches table.
"""
import os
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Optional
from pydantic import BaseModel, Field
from dotenv import load_dotenv

from models.student import CandidateModel, TargetRole, ConfidenceLevel
from models.leads import AxisScores, ScoredLead
from utils.gemini import gemini_json
from utils.helpers import chunk_list
from db.queries import (
    get_leads_for_ranking,
    update_match_initial_score,
    mark_matches_quota_selected,
    update_match_final_score,
    update_candidate_status,
    get_candidate,
)

load_dotenv()
log = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
FRESHER_FLOOR_LPA     = int(os.getenv("FRESHER_BENCHMARK_LPA", "22"))
NON_FRESHER_FLOOR_LPA = int(os.getenv("NON_FRESHER_BENCHMARK_LPA", "35"))
DIRECT_QUOTA          = int(os.getenv("DIRECT_QUOTA", "12"))
INDIRECT_QUOTA        = int(os.getenv("INDIRECT_QUOTA", "8"))
TOTAL_QUOTA           = DIRECT_QUOTA + INDIRECT_QUOTA   # 20
DIRECT_PREFILTER_SIZE = int(os.getenv("DIRECT_PREFILTER_SIZE", "30"))  # Python top-N fed into Gemini pre-filter

# Phase 1A: Stage initial score table (0–10)
STAGE_SCORE_TABLE = {
    "series_a":   10.0,
    "series_b":   10.0,
    "seed":        8.5,
    "series_c":    7.0,
    "pre_seed":    6.5,
    "late_stage":  5.0,
    "unknown":     7.0,   # Neutral — unknown is not disqualifying
    "public":      0.0,   # Hard disqualifier
    "mnc":         0.0,   # Hard disqualifier
}

# Phase 2: Stage multiplier applied to company_stage axis score
STAGE_MULTIPLIERS = {
    "series_a":   1.00,
    "series_b":   1.00,
    "seed":       0.85,
    "series_c":   0.85,
    "pre_seed":   0.70,
    "late_stage": 0.60,
    "public":     0.50,
    "mnc":        0.30,
    "unknown":    0.75,
}

# Phase 2: Axis weights for final_score weighted sum
# Location excluded — all roles are India-based, so it adds no signal.
# Recency is applied as a separate post-scoring multiplier (see _recency_multiplier).
# Domain boosted to 0.25 (from 0.20) — sector/vertical fit is a strong Mesa signal.
#   Offset: skills 0.35→0.32, experience 0.30→0.28.
AXIS_WEIGHTS = {
    "skills":        0.32,
    "experience":    0.28,
    "domain":        0.25,
    "company_stage": 0.15,
}

# Recency multiplier applied to final_score after Gemini Phase 2 scoring.
# Penalises stale listings so a 6-month-old lead cannot outscore a fresh one.
_RECENCY_MULTIPLIER_TABLE = [
    (7,  1.00),   # ≤ 7 days  → no penalty
    (30, 0.95),   # ≤ 30 days → −5%
    (60, 0.88),   # ≤ 60 days → −12%
    (90, 0.82),   # ≤ 90 days → −18%
]
_RECENCY_MULTIPLIER_STALE = 0.75  # > 90 days → −25%

# Phase 1A: Confidence weights for alias matching
_CONFIDENCE_WEIGHT = {
    ConfidenceLevel.high:   1.00,
    ConfidenceLevel.medium: 0.85,
    ConfidenceLevel.low:    0.70,
}
_NO_MATCH_WEIGHT = 0.60


# ── Entry point ───────────────────────────────────────────────────────────────

def rank_leads(candidate_id: str, candidate_model: CandidateModel) -> list[ScoredLead]:
    """
    Main Stage 4 entry point. Reads matches from DB (created by Stage 3),
    runs two-phase scoring, persists all scores, returns final ranked list.

    Args:
        candidate_id:    Supabase UUID of the candidate
        candidate_model: CandidateModel from Stage 2 (target_roles with aliases,
                         dealbreakers, compensation_band, x_factor)

    Returns:
        list[ScoredLead] sorted by final_score descending (rank 1 = best fit)
    """
    update_candidate_status(candidate_id, "ranking")
    log.info("S4: Starting ranking for candidate %s", candidate_id)

    # ── Load all match records from DB ────────────────────────────────────────
    direct_matches, indirect_matches = get_leads_for_ranking(candidate_id)
    log.info("S4: Loaded %d direct + %d indirect match records",
             len(direct_matches), len(indirect_matches))

    if not direct_matches and not indirect_matches:
        log.warning("S4: No leads to score — Stage 3 may have found nothing")
        update_candidate_status(candidate_id, "ranked")
        return []

    # ── Phase 1A: Python initial scoring for ALL direct leads (fast) ──────────
    log.info("S4[1A]: Scoring %d direct leads (Python)", len(direct_matches))
    for match in direct_matches:
        score, breakdown = _score_direct_initial(match, candidate_model)
        match["initial_score"]           = score
        match["initial_score_breakdown"] = breakdown
        update_match_initial_score(match["id"], score, breakdown)

    # ── Phase 1.5: Gemini pre-filter top-N direct leads → pick DIRECT_QUOTA ──
    valid_direct = sorted(
        [m for m in direct_matches if (m.get("initial_score") or 0) > 0],
        key=lambda x: x.get("initial_score", 0),
        reverse=True,
    )
    prefilter_pool = valid_direct[:DIRECT_PREFILTER_SIZE]

    # ── Run Phase 1.5 (direct prefilter) + Phase 1B (indirect) IN PARALLEL ────
    # Both are Gemini-heavy batch jobs with no data dependency on each other.
    # Running them concurrently cuts Phase 1 wall-clock time roughly in half.
    log.info(
        "S4: Starting Phase 1.5 (%d direct) + Phase 1B (%d indirect) in parallel",
        len(prefilter_pool), len(indirect_matches),
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        future_prefilter = executor.submit(
            _gemini_prefilter_direct, prefilter_pool, candidate_model
        ) if len(prefilter_pool) > DIRECT_QUOTA else None

        future_indirect = executor.submit(
            _score_indirect_initial_all, indirect_matches, candidate_model
        ) if indirect_matches else None

        top_direct = future_prefilter.result() if future_prefilter else prefilter_pool
        if future_indirect:
            future_indirect.result()   # Modifies indirect_matches in-place; wait for completion

    log.info("S4: Phase 1 complete — %d direct + %d indirect ready for quota selection",
             len(top_direct), len(indirect_matches))

    # ── Quota selection ───────────────────────────────────────────────────────
    quota_matches = _select_quota(top_direct, indirect_matches)
    mark_matches_quota_selected([m["id"] for m in quota_matches])
    log.info("S4: Quota: %d leads selected for Phase 2 Gemini scoring", len(quota_matches))

    # ── Phase 2: Final Gemini scoring for all quota-selected leads ────────────
    log.info("S4[2]: Final Gemini scoring for %d leads (batch-of-5)", len(quota_matches))
    _final_score_all(quota_matches, candidate_model)

    # ── Assign ranks and persist ──────────────────────────────────────────────
    ranked = _assign_ranks(quota_matches)
    is_fresher = _candidate_is_fresher(candidate_id)

    for match in ranked:
        update_match_final_score(
            match_id        = match["id"],
            final_score     = match.get("final_score", 0.0),
            axis_scores     = match.get("axis_scores", {}),
            rank            = match["rank"],
            rationale       = match.get("rationale", ""),
            below_benchmark = is_below_benchmark(
                match.get("salary_lpa_parsed"),
                is_fresher,
            ),
        )

    update_candidate_status(candidate_id, "ranked")

    if ranked:
        log.info("S4: Complete — #1 lead: %s @ %s (score: %.1f)",
                 ranked[0].get("role_title", "?"),
                 ranked[0].get("company_name", "?"),
                 ranked[0].get("final_score", 0))

    return _to_scored_leads(ranked)


# ── Phase 1A: Direct Lead Initial Scoring (Pure Python) ──────────────────────

def _score_direct_initial(
    match: dict,
    candidate_model: CandidateModel,
) -> tuple[float, dict]:
    """
    Python-only initial scoring for a single direct lead.
    Returns (initial_score 0–10, breakdown_dict).
    Leads with stage_score = 0 (MNC/public) are hard-disqualified from Phase 2.
    """
    stage_label = (match.get("company_stage_label") or "unknown").lower()
    stage_score = STAGE_SCORE_TABLE.get(stage_label, 7.0)

    # Hard disqualifier — MNC or public company
    if stage_score == 0.0:
        return 0.0, {
            "stage":                  0.0,
            "recency":                0.0,
            "confidence_weight":      0.0,
            "stage_label":            stage_label,
            "excluded_by_dealbreaker": True,
        }

    # Hard disqualifier — intern/trainee role for a non-fresher candidate
    role_lower = match.get("role_title", "").lower()
    if any(kw in role_lower for kw in ("intern", "trainee", "apprentice")):
        return 0.0, {
            "stage":                  stage_score,
            "recency":                0.0,
            "confidence_weight":      0.0,
            "stage_label":            stage_label,
            "excluded_by_dealbreaker": True,
            "reason":                 "intern/trainee role excluded for non-fresher",
        }

    recency             = _recency_score(match.get("posted_at"))
    conf_weight, matched_role, matched_conf = _best_alias_match(
        match.get("role_title", ""),
        candidate_model.target_roles,
    )

    score = (stage_score * 0.60 + recency * 0.40) * conf_weight
    score = round(min(score, 10.0), 2)

    return score, {
        "stage":                   stage_score,
        "recency":                 recency,
        "confidence_weight":       conf_weight,
        "matched_role":            matched_role,
        "matched_confidence":      matched_conf,
        "stage_label":             stage_label,
        "excluded_by_dealbreaker": False,
    }


def _recency_score(posted_at) -> float:
    """
    Decay curve by posting age. Applies to both LinkedIn Jobs and Wellfound.
    Returns 5.0 (neutral) when posted_at is absent.
    """
    if posted_at is None:
        return 5.0
    if isinstance(posted_at, str):
        try:
            posted_at = datetime.fromisoformat(posted_at.replace("Z", "+00:00"))
        except Exception:
            return 5.0
    if hasattr(posted_at, "tzinfo") and posted_at.tzinfo is None:
        posted_at = posted_at.replace(tzinfo=timezone.utc)
    try:
        days_old = (datetime.now(timezone.utc) - posted_at).days
    except Exception:
        return 5.0
    if days_old <= 7:   return 10.0
    if days_old <= 30:  return 8.0
    if days_old <= 60:  return 6.0
    return 4.0


def _recency_multiplier(posted_at) -> float:
    """
    Post-scoring penalty for stale listings applied to Phase 2 final_score.
    A 6-month-old lead loses 25% of its Gemini score, ensuring fresh postings
    rank above equally-scored but older ones.
    Returns 1.0 (no penalty) when posted_at is unknown.
    """
    if posted_at is None:
        return 1.0
    if isinstance(posted_at, str):
        try:
            posted_at = datetime.fromisoformat(posted_at.replace("Z", "+00:00"))
        except Exception:
            return 1.0
    if hasattr(posted_at, "tzinfo") and posted_at.tzinfo is None:
        posted_at = posted_at.replace(tzinfo=timezone.utc)
    try:
        days_old = (datetime.now(timezone.utc) - posted_at).days
    except Exception:
        return 1.0
    for max_days, multiplier in _RECENCY_MULTIPLIER_TABLE:
        if days_old <= max_days:
            return multiplier
    return _RECENCY_MULTIPLIER_STALE


def _best_alias_match(
    role_title: str,
    target_roles: list[TargetRole],
) -> tuple[float, str, str]:
    """
    Find the best matching target role using rapidfuzz partial_ratio.
    Checks the primary title AND every alias for each target role.
    Returns (confidence_weight, primary_title_of_best_match, confidence_str).
    Returns (_NO_MATCH_WEIGHT, 'no_match', 'none') if no good match found (< 55 score).
    """
    from rapidfuzz import fuzz

    best_score      = 0.0
    best_role       = None
    best_confidence = None
    role_lower      = role_title.lower()

    for target in target_roles:
        s = fuzz.partial_ratio(role_lower, target.title.lower())
        if s > best_score:
            best_score, best_role, best_confidence = s, target.title, target.confidence

        for alias in (target.aliases or []):
            s = fuzz.partial_ratio(role_lower, alias.lower())
            if s > best_score:
                best_score, best_role, best_confidence = s, target.title, target.confidence

    if best_score < 55 or best_role is None:
        return _NO_MATCH_WEIGHT, "no_match", "none"

    weight = _CONFIDENCE_WEIGHT.get(best_confidence, _NO_MATCH_WEIGHT)
    return weight, best_role, (best_confidence.value if best_confidence else "none")


# ── Phase 1B: Indirect Lead Initial Scoring (Gemini) ─────────────────────────

class _IndirectScore(BaseModel):
    post_index: int   = Field(description="0-based index matching the input list")
    score:      float = Field(ge=0.0, le=100.0, description="0-100 overall fit score")
    reason:     str   = Field(description="One-line explanation")

class _IndirectScoreBatch(BaseModel):
    scores: list[_IndirectScore]


_INDIRECT_SCORE_SYSTEM = (
    "You are a recruitment relevance scorer. Score each LinkedIn post 0-100 on "
    "overall role fit and culture alignment against the candidate profile.\n\n"
    "RULES:\n"
    "- Score on role INTENT and sector/culture fit ONLY.\n"
    "- DO NOT penalise for missing salary, location, or any absent structured field.\n"
    "- A founder writing 'need a killer operator to run everything' scores HIGH for a "
    "Chief of Staff candidate.\n"
    "- Score LOW if the post is clearly about a different function entirely.\n\n"
    "Return JSON: {\"scores\": [{\"post_index\": 0, \"score\": 72, \"reason\": \"...\"}]}"
)


def _score_indirect_initial_all(
    indirect_matches: list[dict],
    candidate_model: CandidateModel,
):
    """
    Phase 1B: Gemini initial scoring for all indirect leads.
    Processes batches of 5 in parallel (max 3 concurrent Gemini calls).
    Writes initial_score to each match dict and to DB.
    """
    context = _build_candidate_context(candidate_model, include_aliases=False)
    system  = f"{_INDIRECT_SCORE_SYSTEM}\n\nCANDIDATE:\n{context}"
    batches = list(chunk_list(indirect_matches, 5))

    _lock = threading.Lock()   # Protect in-place writes to shared list items

    def _score_one_batch(batch: list[dict]) -> None:
        posts_text = "\n\n".join(
            f"[{i}] Company: {m.get('company_name', 'Unknown')}\n"
            f"    Role: {m.get('role_title', 'Unknown')}\n"
            f"    Post: {(m.get('snippet') or m.get('description') or '')[:400]}"
            for i, m in enumerate(batch)
        )
        try:
            result = gemini_json(
                prompt=f"Score these posts:\n\n{posts_text}",
                system=system,
                schema=_IndirectScoreBatch,
            )
            for item in result.scores:
                if 0 <= item.post_index < len(batch):
                    m         = batch[item.post_index]
                    score     = round(item.score, 2)
                    breakdown = {"gemini_scored": True, "reason": item.reason}
                    with _lock:
                        m["initial_score"]           = score
                        m["initial_score_breakdown"] = breakdown
                    update_match_initial_score(m["id"], score, breakdown)
        except Exception as e:
            log.error("S4[1B]: Gemini indirect scoring failed for batch: %s", e)
            for m in batch:
                if "initial_score" not in m:
                    fallback  = {"gemini_scored": False, "error": str(e)}
                    with _lock:
                        m["initial_score"]           = 50.0
                        m["initial_score_breakdown"] = fallback
                    update_match_initial_score(m["id"], 50.0, fallback)

    log.info("S4[1B]: Scoring %d indirect leads in %d parallel batches",
             len(indirect_matches), len(batches))
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(_score_one_batch, batch) for batch in batches]
        for future in as_completed(futures):
            future.result()   # Re-raise any exceptions


# ── Phase 1.5: Gemini Direct Lead Pre-filter ─────────────────────────────────

class _PrefilterScore(BaseModel):
    lead_index: int   = Field(description="0-based index matching the input list")
    score:      float = Field(ge=0.0, le=100.0, description="0-100 fit score")
    reason:     str   = Field(description="One-line explanation")

class _PrefilterBatch(BaseModel):
    scores: list[_PrefilterScore]

_PREFILTER_SYSTEM = (
    "You are a recruitment pre-screener for Mesa School of Business — India's founder-focused MBA. "
    "Score each job listing 0-100 on overall fit against the candidate profile.\n\n"
    "SCORE HIGH when:\n"
    "- Role title closely matches a target role or alias\n"
    "- Company is a funded Indian startup (seed / Series A-C) — NOT MNC, public co, or staffing firm\n"
    "- Domain/sector aligns with candidate's background or sector_fit\n"
    "- Role requires skills the candidate has actually demonstrated\n\n"
    "SCORE LOW when:\n"
    "- Company is an MNC, large conglomerate, or public company\n"
    "- Role is purely field sales, on-ground operations, or manual labour\n"
    "- Role title is far from any target role (even if domain matches)\n"
    "- Job is for a staffing / HR / recruitment agency hiring for a client\n"
    "- Compensation clearly stated and far below candidate's target band\n\n"
    "Return JSON: {\"scores\": [{\"lead_index\": 0, \"score\": 72, \"reason\": \"...\"}]}"
)


def _gemini_prefilter_direct(
    pool: list[dict],
    candidate_model: CandidateModel,
) -> list[dict]:
    """
    Phase 1.5: Send top-N direct leads through Gemini batch-of-5 scoring.
    Batches are processed IN PARALLEL (max 3 concurrent) for speed.
    Returns top DIRECT_QUOTA leads by Gemini score.
    """
    context = _build_candidate_context(candidate_model, include_aliases=True)
    system  = f"{_PREFILTER_SYSTEM}\n\nCANDIDATE:\n{context}"
    batches = list(chunk_list(pool, 5))
    scores: dict[int, float] = {}   # pool_index → gemini_score
    _lock = threading.Lock()

    def _score_one_batch(batch: list[dict], offset: int) -> None:
        leads_text = "\n\n".join(
            f"Lead {offset + i}:\n"
            f"  Company: {m.get('company_name', 'Unknown')}\n"
            f"  Role: {m.get('role_title', 'Unknown')}\n"
            f"  Stage: {m.get('company_stage_label') or 'unknown'}\n"
            f"  Platform: {m.get('source_platform', '')}\n"
            f"  Description: {(m.get('description') or '')[:400]}"
            for i, m in enumerate(batch)
        )
        try:
            result = gemini_json(
                prompt=f"Pre-screen these leads:\n\n{leads_text}",
                system=system,
                schema=_PrefilterBatch,
            )
            with _lock:
                for item in result.scores:
                    if 0 <= item.lead_index < len(pool):
                        scores[item.lead_index] = item.score
        except Exception as e:
            log.error("S4[1.5]: Gemini prefilter failed for batch at offset %d: %s", offset, e)
            with _lock:
                for i in range(len(batch)):
                    idx = offset + i
                    if idx not in scores:
                        scores[idx] = (pool[idx].get("initial_score") or 0) * 10

    log.info("S4[1.5]: Pre-filtering %d leads in %d parallel batches",
             len(pool), len(batches))
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [
            executor.submit(_score_one_batch, batch, i * 5)
            for i, batch in enumerate(batches)
        ]
        for future in as_completed(futures):
            future.result()

    # Sort pool by Gemini prefilter score, return top DIRECT_QUOTA
    ranked = sorted(range(len(pool)), key=lambda i: scores.get(i, 0), reverse=True)
    top = [pool[i] for i in ranked[:DIRECT_QUOTA]]
    log.info(
        "S4[1.5]: Pre-filter complete — top %d from pool of %d (scores: %s)",
        len(top), len(pool),
        ", ".join(f"{scores.get(ranked[i], 0):.0f}" for i in range(len(top))),
    )
    return top


# ── Quota Selection ───────────────────────────────────────────────────────────

def _select_quota(
    top_direct: list[dict],
    indirect_matches: list[dict],
) -> list[dict]:
    """
    Build the final 20-lead quota for Phase 2 Gemini scoring.
    top_direct is already pre-filtered by Phase 1.5 Gemini (at most DIRECT_QUOTA leads).
    Indirect quota expands direct if fewer than INDIRECT_QUOTA indirect leads pass quality bar.
    """
    # Indirect leads below 40 initial score are too noisy to send to Phase 2
    valid_indirect = sorted(
        [m for m in indirect_matches if (m.get("initial_score") or 0) >= 40],
        key=lambda x: x.get("initial_score", 0),
        reverse=True,
    )

    available_indirect = min(len(valid_indirect), INDIRECT_QUOTA)
    direct_quota       = TOTAL_QUOTA - available_indirect   # Expands if indirect is scarce

    selected = top_direct[:direct_quota] + valid_indirect[:available_indirect]

    log.info(
        "S4: Quota — %d direct (Phase 1.5 selected) + %d indirect (of %d valid) = %d total",
        len(top_direct[:direct_quota]),
        available_indirect, len(valid_indirect),
        len(selected),
    )
    return selected


# ── Phase 2: Final Gemini Scoring ─────────────────────────────────────────────

_FINAL_SCORE_SYSTEM = """\
You are an expert recruitment matching engine for Mesa School of Business — \
India's premier founder-focused business school.

Score each job lead against the candidate on 4 axes (1.0–10.0 each). \
All roles are India-based — do NOT factor location into scoring. \
Return a JSON object with a 'scores' array — one item per lead, in the same order as input.

SCORING RULES:
1. HIGH CONFIDENCE target roles must receive stronger skills + experience scores \
than MEDIUM or LOW confidence roles when the role matches.
2. Penalise explicitly when role/company type matches a dealbreaker \
(e.g. MNC, large corp, pure sales).
3. For company_stage_label: infer from description + company name if not provided. \
Use: pre_seed | seed | series_a | series_b | series_c | late_stage | public | mnc | unknown.
4. Source type (job listing vs LinkedIn post) does NOT affect scoring — treat all leads equally.

OUTPUT SCHEMA:
{"scores": [{
  "lead_index": 0,
  "axis_scores": {"skills": 8.5, "experience": 7.0, "domain": 9.0, "company_stage": 8.0},
  "rationale": "One punchy sentence — why this is or is not a fit",
  "company_stage_label": "series_a"
}]}\
"""

class _FinalScoreItem(BaseModel):
    lead_index:          int        = Field(description="0-based index matching input list")
    axis_scores:         AxisScores
    rationale:           str        = Field(description="One punchy sentence — why this is or isn't a fit")
    company_stage_label: str        = Field(
        description="pre_seed|seed|series_a|series_b|series_c|late_stage|public|mnc|unknown"
    )

class _FinalScoreBatch(BaseModel):
    scores: list[_FinalScoreItem]


def _final_score_all(quota_matches: list[dict], candidate_model: CandidateModel):
    """
    Phase 2: Full Gemini scoring for all quota-selected leads in batches of 5.
    Batches are processed IN PARALLEL (max 4 concurrent) for speed.
    Writes final_score, axis_scores, rationale to each match dict in-place.
    """
    context = _build_candidate_context(candidate_model, include_aliases=True)
    system  = f"{_FINAL_SCORE_SYSTEM}\n\nCANDIDATE:\n{context}"
    batches = list(chunk_list(quota_matches, 5))
    _lock   = threading.Lock()

    def _score_one_batch(batch: list[dict]) -> None:
        leads_text = "\n\n".join(
            f"Lead {i}:\n"
            f"  Company: {m.get('company_name', 'Unknown')}\n"
            f"  Role: {m.get('role_title', 'Unknown')}\n"
            f"  Location: {m.get('location', 'India')}\n"
            f"  Stage: {m.get('company_stage_label') or 'unknown'}\n"
            f"  Source: {'LinkedIn Post (informal signal)' if m.get('source_type') == 'indirect' else 'Formal Job Listing'}\n"
            f"  Description: {(m.get('description') or m.get('snippet') or '')[:500]}"
            for i, m in enumerate(batch)
        )
        try:
            result = gemini_json(
                prompt=f"Score these leads:\n\n{leads_text}",
                system=system,
                schema=_FinalScoreBatch,
            )
            for item in result.scores:
                if 0 <= item.lead_index < len(batch):
                    m = batch[item.lead_index]
                    base_score = compute_final_score(
                        item.axis_scores.model_dump(),
                        item.company_stage_label,
                    )
                    raw = round(min(base_score * _recency_multiplier(m.get("posted_at")), 100.0), 2)
                    _stage_now = (item.company_stage_label or "").lower()
                    _role_now  = (m.get("role_title") or "").lower()
                    if _stage_now in ("mnc", "public"):
                        raw = min(raw, 50.0)
                    if any(kw in _role_now for kw in ("intern", "trainee", "apprentice")):
                        raw = min(raw, 45.0)
                    with _lock:
                        m["axis_scores"]         = item.axis_scores.model_dump()
                        m["rationale"]           = item.rationale
                        m["company_stage_label"] = item.company_stage_label
                        m["final_score"]         = raw
        except Exception as e:
            log.error("S4[2]: Gemini final scoring failed for batch: %s", e)
            for m in batch:
                if "final_score" not in m:
                    with _lock:
                        m["final_score"] = round((m.get("initial_score") or 5.0) * 10, 2)
                        m["axis_scores"] = {}
                        m["rationale"]   = "Scoring unavailable — using initial score as fallback"

    log.info("S4[2]: Final Gemini scoring for %d leads in %d parallel batches",
             len(quota_matches), len(batches))
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(_score_one_batch, batch) for batch in batches]
        for future in as_completed(futures):
            future.result()


# ── Scoring Math ──────────────────────────────────────────────────────────────

def compute_final_score(axis_scores: dict, stage_label: str) -> float:
    """
    Weighted sum of Gemini axis scores (1–10 each) × axis weights, scaled to 100.
    Stage multiplier is applied to the company_stage axis score before the sum.

    Max = 10 × 10 (all axes perfect) = 100.
    """
    multiplier = STAGE_MULTIPLIERS.get((stage_label or "unknown").lower(), 0.75)
    adjusted   = {
        **axis_scores,
        "company_stage": axis_scores.get("company_stage", 5.0) * multiplier,
    }
    weighted = sum(
        adjusted.get(ax, 5.0) * weight
        for ax, weight in AXIS_WEIGHTS.items()
    )
    return round(min(weighted * 10, 100.0), 2)


def is_below_benchmark(salary_lpa: Optional[float], is_fresher: bool) -> bool:
    """
    Flag leads where the listed market salary is significantly below the Mesa placement floor.
    Returns False when salary_lpa is None (no data — do not penalise).
    """
    if salary_lpa is None:
        return False
    floor = FRESHER_FLOOR_LPA if is_fresher else NON_FRESHER_FLOOR_LPA
    return salary_lpa < floor * 0.85


# ── Rank Assignment ───────────────────────────────────────────────────────────

def _assign_ranks(scored_matches: list[dict]) -> list[dict]:
    """Sort quota-selected matches by final_score descending and assign rank 1–N."""
    ranked = sorted(
        scored_matches,
        key=lambda x: x.get("final_score", 0.0),
        reverse=True,
    )
    for i, match in enumerate(ranked):
        match["rank"] = i + 1
    return ranked


# ── Output Conversion ─────────────────────────────────────────────────────────

def _to_scored_leads(ranked_matches: list[dict]) -> list[ScoredLead]:
    """Convert ranked match dicts to ScoredLead Pydantic models for downstream use."""
    results: list[ScoredLead] = []
    for m in ranked_matches:
        try:
            results.append(ScoredLead(
                company_name             = m.get("company_name", ""),
                role_title               = m.get("role_title", ""),
                description              = m.get("description") or m.get("snippet") or "",
                key_requirements         = m.get("key_requirements") or [],
                location                 = m.get("location") or "India",
                remote_ok                = m.get("remote_ok", False),
                source_channel           = m.get("source_type", ""),
                source_platform          = m.get("source_platform") or m.get("platform") or "",
                post_url                 = m.get("post_url") or m.get("signal_url"),
                hiring_manager_name      = m.get("hiring_manager_name"),
                hiring_manager_linkedin  = m.get("hiring_manager_linkedin"),
                company_stage_label      = m.get("company_stage_label"),
                job_id                   = m.get("id"),      # match UUID (used by S5/S6)
                fit_score                = m.get("final_score", 0.0),
                axis_scores              = m.get("axis_scores", {}),
                rationale                = m.get("rationale", ""),
                rank                     = m.get("rank"),
                below_mesa_benchmark     = m.get("below_mesa_benchmark", False),
            ))
        except Exception as e:
            log.warning("S4: Failed to convert match to ScoredLead: %s", e)
    return results


# ── Shared Helpers ────────────────────────────────────────────────────────────

def _build_candidate_context(candidate_model: CandidateModel, include_aliases: bool) -> str:
    """Build a concise candidate context block injected into every Gemini prompt."""
    roles_lines = []
    for r in candidate_model.target_roles:
        line = f'  - "{r.title}" ({r.confidence.value.upper()})'
        if include_aliases and r.aliases:
            line += f" — also known as: {', '.join(r.aliases)}"
        roles_lines.append(line)

    sectors      = ", ".join(candidate_model.sector_fit)
    dealbreakers = "\n".join(f"  - {d}" for d in candidate_model.dealbreakers)
    comp = (
        f"{candidate_model.compensation_band.low_lpa}L–"
        f"{candidate_model.compensation_band.high_lpa}L INR"
        if candidate_model.compensation_band else "Not specified"
    )
    return (
        f"Target roles:\n" + "\n".join(roles_lines) + "\n"
        f"Sector fit: {sectors}\n"
        f"Compensation band: {comp}\n"
        f"Dealbreakers:\n{dealbreakers}\n"
        f"X-factor: {candidate_model.x_factor}"
    )


def _candidate_is_fresher(candidate_id: str) -> bool:
    """Fetch the is_fresher flag from the candidate's student_profile in Supabase."""
    try:
        candidate = get_candidate(candidate_id)
        if candidate:
            return candidate.get("student_profile", {}).get("is_fresher", False)
    except Exception:
        pass
    return False
