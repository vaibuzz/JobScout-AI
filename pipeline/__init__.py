"""
Mesa Careers AI — 6-stage agentic pipeline.

Usage:
    from pipeline import run_full_pipeline
    result = run_full_pipeline(linkedin_url="https://linkedin.com/in/...")
    result = run_full_pipeline(pdf_path="/path/to/resume.pdf")
"""

from .s1_ingest     import ingest_profile
from .s2_synthesise import synthesise_candidate
from .s3_discover   import discover_leads
from .s4_rank       import rank_leads
from .s5_outreach   import generate_outreach_on_demand
from .s6_dossier    import generate_dossier


def run_full_pipeline(
    linkedin_url: str | None = None,
    pdf_path: str | None = None,
    student_name: str = "",
) -> dict:
    """
    Run all 6 stages end-to-end.
    Returns a dict with keys: candidate_id, ranked_leads, status.
    Stages 5 and 6 are lazy — triggered separately on user click.
    """
    if not linkedin_url and not pdf_path:
        raise ValueError("Provide either linkedin_url or pdf_path")

    # S1 — Ingest
    student_profile, candidate_id = ingest_profile(
        linkedin_url=linkedin_url,
        pdf_path=pdf_path,
        student_name=student_name,
    )

    # S2 — Synthesise
    candidate_model = synthesise_candidate(candidate_id, student_profile)

    # S3 — Discover
    raw_leads = discover_leads(
        candidate_id,
        candidate_model.search_queries.model_dump(),
    )

    # S4 — Rank
    ranked_leads = rank_leads(candidate_id, raw_leads, candidate_model)

    return {
        "candidate_id":  candidate_id,
        "ranked_leads":  ranked_leads,
        "status":        "complete",
    }
