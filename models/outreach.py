"""
Outreach and dossier Pydantic models (Stage 5 & 6 output schemas).
"""
from __future__ import annotations
from typing import Optional
from datetime import datetime
from pydantic import BaseModel, Field


class EmailDraft(BaseModel):
    subject: str = Field(description="Direct 6-8 word email subject. No clickbait.")
    body:    str = Field(description="5-7 sentence email body from Mesa Placement Director persona")


class OutreachDraft(BaseModel):
    """
    Stage 5 output — generated lazily on button click.

    Two scenarios:
      - hiring_manager_identified=True:  named email + 3-sentence LinkedIn DM
      - hiring_manager_identified=False: company-directed email + 300-char
                                         LinkedIn connection request note template.
                                         UI shows "Manual research needed" badge.
    """
    email:                        EmailDraft
    dm:                           str = Field(
        description="If HM identified: 3-sentence LinkedIn DM ending with yes/no question. "
                    "If HM not identified: LinkedIn connection request note, max 300 characters.",
    )
    personalisation_note:         str = Field(
        description="One thing careers team MUST do before sending. "
                    "If no HM: specific action to find the right contact on LinkedIn.",
    )
    outreach_hook:                str = Field(
        description="One sentence linking candidate's specific background to this job's core need. "
                    "Stored in matches table and shown to careers team for context.",
    )
    hiring_manager_email:         Optional[str] = Field(
        default=None,
        description="Email found via Apollo/Hunter. None if not found.",
    )
    hiring_manager_identified:    bool = Field(
        default=True,
        description="False when hiring manager name is absent from the lead. "
                    "Drives 'Manual research needed' badge in the UI.",
    )
    email_found:                  bool = Field(
        default=False,
        description="True if Apollo/Hunter returned a verified email address.",
    )
    generated_at:                 datetime = Field(default_factory=datetime.utcnow)


class DossierOutput(BaseModel):
    """
    Stage 6 output — 1-page internal memo for the Mesa Careers Team.
    Pure Markdown content, plus metadata.
    """
    markdown_content:     str = Field(
        description="Full dossier in Markdown. 5 sections: Company Snapshot, "
                    "Role Context, Why [Student] Fits, Likely Objections, Competitive Landscape.",
    )
    company_name:         str
    role_title:           str
    candidate_name:       str
    generated_at:         datetime = Field(default_factory=datetime.utcnow)
    tavily_sources:       list[str] = Field(default_factory=list,
                                             description="URLs used for grounding the dossier")
