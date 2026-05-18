-- Mesa Careers AI — Supabase Schema v2
-- Two-table lead architecture replacing the single jobs table.
--
--   direct_leads   → structured data from Apify LinkedIn Jobs + Wellfound
--   indirect_leads → unstructured signals from Apify LinkedIn Post Scraper
--   matches        → two-phase scoring output (initial pre-filter + final Gemini)
--
-- MIGRATION from v1: run these drops first if upgrading an existing database:
--   DROP TABLE IF EXISTS matches CASCADE;
--   DROP TABLE IF EXISTS jobs    CASCADE;

create extension if not exists "uuid-ossp";

-- ── candidates ───────────────────────────────────────────────────────────────
-- Stores Stage 1 (normalised profile) and Stage 2 (synthesis) output per student.
-- One row per pipeline run.
create table if not exists candidates (
  id                    uuid primary key default uuid_generate_v4(),
  created_at            timestamptz default now(),
  updated_at            timestamptz default now(),

  -- Identity
  name                  text not null,
  linkedin_url          text,
  input_type            text check (input_type in ('url', 'pdf')),

  -- Stage 1: normalised profile (full StudentProfile Pydantic model as JSONB)
  student_profile       jsonb not null,

  -- Stage 2: Gemini synthesis
  target_roles          jsonb,     -- [{title, confidence}, ...]
  sector_fit            jsonb,     -- ["Esports", "D2C", ...]
  compensation_band_inr text,      -- "30L - 45L"
  dealbreakers          jsonb,     -- ["Large corps > 1000 employees", ...]
  search_queries        jsonb,     -- {formal_platforms: [...], hidden_signals: [...]}
  x_factor              text,      -- One-sentence differentiator for Stage 5 hook

  -- Pipeline lifecycle
  pipeline_status       text default 'pending'
    check (pipeline_status in (
      'pending',
      'ingested',
      'synthesised',
      'discovering',
      'discovered',
      'ranking',
      'ranked',
      'complete',
      'error'
    )),
  error_message         text
);

-- ── direct_leads ──────────────────────────────────────────────────────────────
-- Source: Apify LinkedIn Jobs Scraper and Apify Wellfound Scraper.
-- Data is structured, complete, and trusted — every field should be populated.
-- Dedup key: (company_name, role_title, source_platform).
create table if not exists direct_leads (
  id                      uuid primary key default uuid_generate_v4(),
  created_at              timestamptz default now(),

  -- Core job data
  company_name            text not null,
  role_title              text not null,
  description             text,
  key_requirements        jsonb,               -- ["5+ yrs experience", "Python skills", ...]
  location                text,
  remote_ok               boolean default false,
  salary_estimate         text,                -- Raw salary string from job listing
  salary_lpa_parsed       numeric(5,1),        -- Parsed numeric LPA (null if not extractable)

  -- Source provenance
  source_platform         text not null
    check (source_platform in ('LinkedIn Jobs', 'Wellfound')),
  post_url                text,
  posted_at               timestamptz,         -- Set for both LinkedIn Jobs (postedAt) and Wellfound (live_start_at)
                                               -- Recency multiplier skipped only when this is NULL
  apify_job_id            text,                -- Apify's unique job ID (intra-channel dedup key)

  -- Company intelligence (populated from Wellfound data or inferred)
  company_stage_label     text
    check (company_stage_label in (
      'pre_seed', 'seed',
      'series_a', 'series_b', 'series_c',
      'late_stage', 'public', 'mnc', 'unknown'
    )),

  -- Hiring manager (formal listings rarely expose this — usually null)
  hiring_manager_name     text,
  hiring_manager_linkedin text,

  -- Dedup constraint
  unique (company_name, role_title, source_platform)
);

-- ── indirect_leads ────────────────────────────────────────────────────────────
-- Source: Apify LinkedIn Post Scraper.
-- Raw LinkedIn posts from founders/hiring managers — unstructured text.
-- company_name and role_title are extracted by Gemini from the post text.
-- hiring_manager data comes from the post AUTHOR via Apify — reliable, not inferred.
-- Dedup key: signal_url (each LinkedIn post URL is globally unique).
create table if not exists indirect_leads (
  id                      uuid primary key default uuid_generate_v4(),
  created_at              timestamptz default now(),

  -- Gemini-extracted fields from post text
  company_name            text not null,
  role_title              text not null,       -- Inferred from context if not explicit
  snippet                 text,                -- Raw post text, truncated to 2000 chars

  -- Source provenance
  signal_url              text not null unique, -- Direct URL to the LinkedIn post
  platform                text default 'LinkedIn Post',
  posted_at               timestamptz,          -- From Apify post metadata (reliable)

  -- Post author = hiring manager (data sourced directly from Apify, not inferred)
  hiring_manager_name     text,
  hiring_manager_linkedin text,                 -- Author's actual LinkedIn profile URL
  engagement_score        integer,              -- likes + comments — proxy for signal quality

  -- Extraction quality flags
  role_inferred           boolean default false, -- True if role was inferred, not explicitly stated
  extraction_confidence   text
    check (extraction_confidence in ('high', 'medium', 'low'))
);

-- ── matches ───────────────────────────────────────────────────────────────────
-- Stage 4 output — two-phase scored and ranked leads per candidate.
--
-- Two-phase scoring design:
--   Phase 1 (initial_score): Fast pre-filter. Python math for direct leads,
--            Gemini batch-of-5 for indirect. Used ONLY for quota selection.
--   Phase 2 (final_score):   Full Gemini scoring for quota-selected top 20.
--            Both channels get identical scoring. final_score determines rank.
--
-- Exactly one of direct_lead_id / indirect_lead_id must be non-null.
-- Enforced by chk_matches_single_lead CHECK constraint.
create table if not exists matches (
  id                       uuid primary key default uuid_generate_v4(),
  created_at               timestamptz default now(),

  candidate_id             uuid references candidates(id) on delete cascade,

  -- Lead reference — exactly one non-null (enforced by constraint below)
  direct_lead_id           uuid references direct_leads(id) on delete cascade,
  indirect_lead_id         uuid references indirect_leads(id) on delete cascade,
  source_type              text not null check (source_type in ('direct', 'indirect')),

  constraint chk_matches_single_lead check (
    (direct_lead_id   is not null)::int +
    (indirect_lead_id is not null)::int = 1
  ),

  -- ── Phase 1: Initial scoring (pre-filter only, NOT the final rank) ──────────
  -- direct leads:   Python weighted sum — role_match, salary_proximity, recency, stage
  -- indirect leads: Gemini batch scoring — role intent, culture fit (no salary/location penalty)
  initial_score            numeric(5,2),
  initial_score_breakdown  jsonb,       -- {role_match: 8.2, salary_proximity: 0.7, ...}
  quota_selected           boolean default false, -- True = this lead advanced to Phase 2

  -- ── Phase 2: Final scoring (Gemini for all quota-selected leads) ────────────
  -- Both direct and indirect get the same Gemini prompt. final_score = the rank.
  final_score              numeric(5,2),
  axis_scores              jsonb,       -- {skills, experience, domain, company_stage, location}
  rank                     integer,     -- 1 = best fit. Null until Phase 2 completes.
  rationale                text,        -- 1-sentence fit explanation from Gemini
  below_mesa_benchmark     boolean default false, -- Red flag: market salary < 85% of Mesa floor

  -- ── Stage 5: Outreach (lazy — all fields populated on button click) ─────────
  -- outreach_hook is generated HERE (Stage 5), not during Stage 4 scoring.
  outreach_hook            text,        -- Specific link between candidate background + job need
  email_subject            text,        -- 6-8 word subject line
  email_body               text,        -- 5-7 sentence email from Mesa Director persona
  dm_draft                 text,        -- 3-sentence LinkedIn DM variant
  personalisation_note     text,        -- One thing careers team must verify before sending
  hiring_manager_email     text,        -- Found via Apollo → Hunter in Stage 5 (null if not found)
  outreach_generated_at    timestamptz,

  -- ── Stage 6: Dossier (rank=1 lead only) ─────────────────────────────────────
  dossier_markdown         text,
  dossier_generated_at     timestamptz
);

-- Partial unique indexes (NULL-safe) — prevents duplicate matches per candidate per lead.
-- Standard UNIQUE constraints don't handle NULLs correctly in PostgreSQL.
create unique index if not exists idx_matches_direct_unique
  on matches (candidate_id, direct_lead_id)
  where direct_lead_id is not null;

create unique index if not exists idx_matches_indirect_unique
  on matches (candidate_id, indirect_lead_id)
  where indirect_lead_id is not null;

-- Performance indexes
create index if not exists idx_matches_candidate   on matches (candidate_id);
create index if not exists idx_matches_final_score on matches (final_score desc);
create index if not exists idx_matches_rank        on matches (candidate_id, rank);
create index if not exists idx_direct_company      on direct_leads (company_name);
create index if not exists idx_direct_platform     on direct_leads (source_platform);
create index if not exists idx_indirect_url        on indirect_leads (signal_url);
create index if not exists idx_candidates_status   on candidates (pipeline_status);
create index if not exists idx_candidates_url      on candidates (linkedin_url);