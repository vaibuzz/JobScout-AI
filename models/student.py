"""
Core Pydantic models: StudentProfile and CandidateModel.

StudentProfile  — output of Stage 1 (normalised from PDF or LinkedIn scrape)
CandidateModel  — output of Stage 2 (Gemini synthesis of positioning + search queries)
"""
from __future__ import annotations
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


# ── Enums ─────────────────────────────────────────────────────────────────────

class Seniority(str, Enum):
    junior  = "junior"
    mid     = "mid"
    senior  = "senior"
    founder = "founder"


class ConfidenceLevel(str, Enum):
    high   = "high"
    medium = "medium"
    low    = "low"


# ── StudentProfile (Stage 1 output) ───────────────────────────────────────────

class RoleHistory(BaseModel):
    title:       str = Field(description="Job title held")
    company:     str = Field(description="Company name")
    duration:    str = Field(description="Duration e.g. '2021-2023' or '2 years'")
    description: str = Field(default="", description="Role responsibilities / achievements")


class StudentProfile(BaseModel):
    """
    Normalised student profile — identical schema regardless of whether
    the input was a PDF resume or a LinkedIn profile scrape via Apify.
    """
    name:         str            = Field(description="Full name")
    headline:     str            = Field(description="Current role or professional identity")
    location:     str            = Field(default="Not specified", description="City, Country")
    skills:       list[str]      = Field(default_factory=list, max_length=20,
                                         description="Specific technical and soft skills, max 20")
    role_history: list[RoleHistory] = Field(default_factory=list,
                                             description="Work experience in reverse chronological order")
    domains:      list[str]      = Field(default_factory=list, min_length=1, max_length=5,
                                          description="Industry domains e.g. ['Fintech', 'D2C', 'SaaS']")
    seniority:    Seniority      = Field(description="Career seniority level")
    preferences:  Optional[list[str]] = Field(
        default=None,
        description="Inferred work preferences e.g. ['early-stage', 'equity-driven']. "
                    "Only populate if clearly evidenced in the profile.",
    )
    education:    list[str]      = Field(default_factory=list,
                                          description="Degrees and institutions")
    is_fresher:   bool           = Field(
        description="True if total work experience < 2 years. "
                    "Determines Mesa placement benchmark (22 LPA vs 35 LPA).",
    )

    # Input metadata (set by S1, not Gemini)
    input_type:   str            = Field(default="unknown",
                                          description="'pdf' or 'apify_url'")
    linkedin_url: Optional[str]  = Field(default=None)


# ── CandidateModel (Stage 2 output) ───────────────────────────────────────────

class TargetRole(BaseModel):
    title:      str             = Field(description="Job title to target e.g. 'Chief of Staff'")
    confidence: ConfidenceLevel = Field(description="Confidence level: high=evidenced, medium=inferred, low=stretch")
    aliases:    list[str]       = Field(
        default_factory=list,
        description="2-3 common alternative titles for this role. "
                    "E.g. 'Chief of Staff' → ['Head of CEO Office', 'CoS', 'Office of the CEO']. "
                    "Used by Stage 3 for broader Apify search queries and by Stage 4 Phase 1A alias matching.",
    )


class SearchQueries(BaseModel):
    formal_platforms: list[str] = Field(
        default_factory=list,
        description="Primary job title strings for Apify LinkedIn Jobs + Wellfound Scrapers. "
                    "Use only the primary role title — NOT aliases. Max 3 strings.",
    )
    hidden_signals: list[str] = Field(
        default_factory=list,
        description="Search phrases for Apify LinkedIn Post Scraper to surface founder hiring posts. "
                    "MUST start with '#Hiring' followed by a startup-specific role title or alias. "
                    "E.g. '#Hiring Chief of Staff', '#Hiring Head of Growth', '#Hiring VP Product'. "
                    "Do NOT use generic titles (e.g. 'Sales Manager') or add location suffixes. "
                    "Max 5 strings.",
    )


class CompensationBand(BaseModel):
    low_lpa:  int = Field(description="Lower bound in LPA (Lakhs Per Annum)")
    high_lpa: int = Field(description="Upper bound in LPA")


class CandidateModel(BaseModel):
    """
    Strategic positioning model derived by Gemini from StudentProfile.
    Drives all downstream stages (S3 search, S4 scoring, S5 outreach, S6 dossier).
    """
    target_roles:          list[TargetRole]     = Field(
        description="Top 2-3 best-fit roles ordered by confidence. Hard max: 3 roles.",
        max_length=3,
    )
    sector_fit:            list[str]            = Field(description="Industry sectors that match the candidate")
    compensation_band:     Optional[CompensationBand] = Field(
        default=None,
        description="Expected compensation band. Set to null in Gemini prompt — "
                    "filled afterwards by Tavily comp grounding.",
    )
    dealbreakers:          list[str]            = Field(
        description="Company/role types that are bad fits e.g. 'MNC > 1000 employees'",
    )
    x_factor:              str                  = Field(
        description="ONE specific sentence: what makes this candidate uniquely hard to replicate. "
                    "NOT generic. E.g. 'Built and sold an esports platform to 50k users while at college'.",
    )
    search_queries:        SearchQueries        = Field(description="Pre-built search strings for S3")
