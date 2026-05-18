"""
Lead-related Pydantic models.

RawLead   — S3 output: a single job lead before scoring
AxisScores — S4 intermediate: 5-axis scoring from Gemini
ScoredLead — S4 output: a lead with fit_score and ranking metadata
"""
from __future__ import annotations
from typing import Optional
from datetime import datetime
from pydantic import BaseModel, Field


class RawLead(BaseModel):
    """
    A single job opportunity discovered in Stage 3.
    Created from both Channel A (Apify LinkedIn Jobs) and
    Channel B (Tavily hidden signals) results.
    """
    company_name:              str           = Field(description="Company name")
    role_title:                str           = Field(description="Job title or inferred role")
    description:               str           = Field(default="", description="Job description or post summary")
    key_requirements:          list[str]     = Field(default_factory=list,
                                                      description="Extracted requirements from job post")
    location:                  str           = Field(default="India",
                                                      description="Job location or 'Remote'")
    remote_ok:                 bool          = Field(default=False)
    compensation_estimate:     Optional[str] = Field(default=None,
                                                      description="Compensation if mentioned in listing")
    source_channel:            str           = Field(description="'formal' (Apify) or 'hidden' (Tavily)")
    source_platform:           str           = Field(description="'LinkedIn Jobs', 'Wellfound', 'Tavily', etc.")
    post_url:                  Optional[str] = Field(default=None, description="Direct URL to the job post")
    posted_at:                 Optional[datetime] = Field(default=None, description="Posting date if available")
    apify_job_id:              Optional[str] = Field(default=None,
                                                      description="Apify unique job ID for intra-channel dedup")
    hiring_manager_name:       Optional[str] = Field(default=None)
    hiring_manager_linkedin:   Optional[str] = Field(default=None)
    company_stage_label:       Optional[str] = Field(
        default=None,
        description="'pre_seed' | 'seed' | 'series_a' | 'series_b' | 'series_c' | "
                    "'late_stage' | 'public' | 'mnc' | 'unknown'",
    )
    market_salary_lpa:         Optional[int] = Field(
        default=None,
        description="Market salary from AmbitionBox/Glassdoor search (Stage 4)",
    )

    # Internal dedup key (set at insert time)
    job_id:                    Optional[str] = Field(default=None,
                                                      description="Supabase UUID after upsert")


class AxisScores(BaseModel):
    """Gemini scoring output for a single job (Stage 4 batch scoring)."""
    skills:        float = Field(ge=1.0, le=10.0, description="Skill overlap score")
    experience:    float = Field(ge=1.0, le=10.0, description="Seniority and years-of-experience match")
    domain:        float = Field(ge=1.0, le=10.0, description="Industry/vertical overlap")
    company_stage: float = Field(ge=1.0, le=10.0, description="Raw stage score (Python applies multiplier)")
    location:      float = Field(default=10.0, ge=1.0, le=10.0, description="Geography — all roles are India-based, excluded from final ranking weight")


class GeminiScoredJob(BaseModel):
    """Schema for one item in Stage 4 Phase 2 Gemini final scoring response."""
    lead_index:          int        = Field(description="0-based index of the lead in the input batch — used to match result back to input list")
    axis_scores:         AxisScores
    rationale:           str        = Field(description="One punchy sentence: why this is or isn't a fit")
    company_stage_label: str        = Field(
        description="Inferred or confirmed stage: 'pre_seed' | 'seed' | 'series_a' | 'series_b' | "
                    "'series_c' | 'late_stage' | 'public' | 'mnc' | 'unknown'",
    )


class ScoredLead(BaseModel):
    """A lead enriched with fit_score after Stage 4 scoring."""
    # All RawLead fields
    company_name:            str
    role_title:              str
    description:             str           = ""
    key_requirements:        list[str]     = Field(default_factory=list)
    location:                str           = "India"
    remote_ok:               bool          = False
    compensation_estimate:   Optional[str] = None
    source_channel:          str           = ""
    source_platform:         str           = ""
    post_url:                Optional[str] = None
    posted_at:               Optional[datetime] = None
    hiring_manager_name:     Optional[str] = None
    hiring_manager_linkedin: Optional[str] = None
    company_stage_label:     Optional[str] = None
    market_salary_lpa:       Optional[int] = None
    job_id:                  Optional[str] = None
    apify_job_id:            Optional[str] = None

    # Scoring fields
    fit_score:               float
    axis_scores:             dict          = Field(default_factory=dict)
    salary_score:            float         = 0.0   # Bonus from AmbitionBox/Glassdoor
    rationale:               str           = ""
    outreach_hook:           str           = ""
    rank:                    Optional[int] = None
    below_mesa_benchmark:    bool          = False
