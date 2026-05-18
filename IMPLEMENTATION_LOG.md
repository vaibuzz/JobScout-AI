# Mesa Careers AI — Implementation Log
> Single source of truth for all architectural decisions, changes, and implementation steps.
> Updated: May 2026

---

## 1. Project Overview

**Mesa Careers AI** is a 6-stage agentic pipeline that takes a LinkedIn URL or resume PDF and produces ranked job leads, personalised outreach drafts, and an internal dossier — all browsable in a Streamlit dashboard.

**Mesa Placement Benchmarks:**
- Freshers (< 2 yrs exp): 22 LPA floor
- Non-freshers: 35 LPA floor

---

## 2. Tech Stack

| Layer | Tool | Notes |
|---|---|---|
| LLM | Gemini 2.5 Flash | `gemini-2.5-flash` via `google-genai` SDK (NOT deprecated `google-generativeai`) |
| UI | Streamlit | Progressive rendering via `st.empty()` |
| Profile (URL) | Proxycurl API | `PROXYCURL_API_KEY` |
| Profile (PDF) | pdfplumber + Gemini | Fallback when URL scrape fails |
| Formal jobs | Apify LinkedIn Jobs Scraper | Actor: `curious_coder/linkedin-jobs-scraper` |
| Startup jobs | Apify Wellfound Scraper | Actor: `curious_coder/wellfound-jobs-scraper` |
| Hiring signals | Apify LinkedIn Post Scraper | Replaces Tavily for indirect channel |
| Web search | Tavily Search API | Used for: comp grounding (S2), company stage enrichment (S3), dossier research (S6) |
| Email lookup | Apollo.io → Hunter.io fallback | `APOLLO_API_KEY`, `HUNTER_API_KEY` |
| Database | Supabase (Postgres) | `SUPABASE_URL`, `SUPABASE_KEY` |
| Validation | Pydantic v2 | Every Gemini JSON output validated |
| Fuzzy dedup | rapidfuzz | Cross-channel duplicate detection |
| PDF export | weasyprint | Dossier download button |
| Retry logic | tenacity | All external API calls |

---

## 3. File Structure

```
mesa-careers-ai/
├── app.py                      ← Streamlit dashboard (SCAFFOLD — not yet implemented)
├── requirements.txt
├── .env.example
├── Dockerfile / docker-compose.yml
│
├── pipeline/
│   ├── s1_ingest.py            ← COMPLETE
│   ├── s2_synthesise.py        ← COMPLETE — needs update (aliases + 3-role cap)
│   ├── s3_discover.py          ← COMPLETE — needs update (3 targeted changes)
│   ├── s4_rank.py              ← STUB — full plan documented below
│   ├── s5_outreach.py          ← STUB — plan documented below
│   └── s6_dossier.py           ← STUB — plan documented below
│
├── models/
│   ├── student.py              ← needs update (aliases on TargetRole, 3-role cap)
│   ├── leads.py                ← needs update (remove outreach_hook from GeminiScoredJob)
│   └── outreach.py             ← COMPLETE (OutreachDraft, DossierOutput schemas)
│
├── db/
│   ├── schema.sql              ← REWRITTEN (v2, two-table architecture)
│   ├── queries.py              ← REWRITTEN (new functions for direct/indirect leads)
│   └── client.py              ← COMPLETE (Supabase singleton)
│
└── utils/
    ├── gemini.py               ← COMPLETE (gemini_json, gemini_text, gemini_extract_salary_lpa)
    ├── search.py               ← COMPLETE (tavily_search)
    ├── enrichment.py           ← COMPLETE (find_email: Apollo → Hunter)
    └── helpers.py              ← COMPLETE (is_duplicate, chunk_list)
```

---

## 4. Current Stage Completion Status

| Stage | File | Status | Notes |
|---|---|---|---|
| S1 — Profile Ingestion | `pipeline/s1_ingest.py` | ✅ Complete | No changes needed |
| S2 — Candidate Synthesis | `pipeline/s2_synthesise.py` | ✅ Complete | Prompt updated: 3 roles max + aliases |
| S3 — Lead Discovery | `pipeline/s3_discover.py` | ✅ Complete | Full rewrite — Apify Post Scraper, batched calls, company stage enrichment |
| S4 — Scoring & Ranking | `pipeline/s4_rank.py` | ✅ Complete | Two-phase scoring, quota, Gemini final scorer |
| S5 — Outreach Drafts | `pipeline/s5_outreach.py` | ✅ Complete | Lazy, Mesa Director persona, null HM handling |
| S6 — Internal Dossier | `pipeline/s6_dossier.py` | ✅ Complete | Parallel Tavily, section validation, PDF export |
| Streamlit UI | `app.py` | ✅ Complete | State-machine, 8 views, animated Mesa branding |
| DB Schema | `db/schema.sql` | ✅ Complete | v2: direct_leads + indirect_leads + matches |
| DB Queries | `db/queries.py` | ✅ Complete | All functions for v2 schema + get_leads_for_ranking |
| Models | `models/student.py` | ✅ Complete | TargetRole has aliases, max 3 roles |
| Models | `models/leads.py` | ✅ Complete | GeminiScoredJob: lead_index, no outreach_hook |
| Models | `models/outreach.py` | ✅ Complete | OutreachDraft: outreach_hook + hiring_manager_identified |

---

## 5. DB Schema v2 — Key Architecture Decision

### Why Two Tables Instead of One `jobs` Table

The original schema had a single `jobs` table with a `source_channel` column (`'formal'` or `'hidden'`). This was replaced with two distinct tables because structured and unstructured data have fundamentally different fields, validation rules, and scoring logic. Mixing them caused nulls everywhere and ambiguous queries.

### Table: `direct_leads`
- **Source:** Apify LinkedIn Jobs Scraper + Apify Wellfound Scraper
- **Nature:** Structured, trusted, complete
- **Dedup key:** `(company_name, role_title, source_platform)`
- **Key fields:** `company_name`, `role_title`, `description`, `key_requirements`, `salary_estimate`, `salary_lpa_parsed`, `source_platform`, `post_url`, `posted_at`, `apify_job_id`, `company_stage_label`, `hiring_manager_name`, `hiring_manager_linkedin`
- **`posted_at`:** Set for LinkedIn Jobs. Also set for Wellfound (actor now returns it). **Recency scoring applies to both.**

### Table: `indirect_leads`
- **Source:** Apify LinkedIn Post Scraper (replaces Tavily for primary indirect channel)
- **Nature:** Unstructured post text, partially extracted by Gemini
- **Dedup key:** `signal_url` (each LinkedIn post URL is globally unique)
- **Key fields:** `company_name`, `role_title`, `snippet`, `signal_url`, `platform`, `posted_at`, `hiring_manager_name`, `hiring_manager_linkedin` (reliable — from post author, NOT inferred), `engagement_score`, `role_inferred`, `extraction_confidence`
- **hiring_manager data:** Comes directly from Apify post author — reliable, not guessed

### Table: `matches` (v2)
Major rewrite from v1. Key additions:
- **Two nullable FKs:** `direct_lead_id` + `indirect_lead_id` with CHECK constraint (exactly one non-null)
- **`source_type`:** `'direct'` or `'indirect'`
- **Two-phase scoring:**
  - `initial_score` + `initial_score_breakdown` + `quota_selected` → Phase 1 (pre-filter)
  - `final_score` + `axis_scores` + `rank` + `rationale` → Phase 2 (Gemini, determines actual rank)
- **`outreach_hook`:** Column exists but is written by **Stage 5**, NOT Stage 4
- **`email_subject` + `email_body`:** Split columns (matches `EmailDraft` Pydantic model)
- **`personalisation_note`:** Added (was missing from v1)
- **Partial unique indexes** for NULL-safe dedup (standard UNIQUE doesn't handle NULLs)

### New DB Functions in `queries.py`

| Old (v1) | New (v2) |
|---|---|
| `upsert_job()` | `upsert_direct_lead()` + `upsert_indirect_lead()` |
| `get_job()` | `get_direct_lead()` + `get_indirect_lead()` |
| `upsert_match()` | `create_match_direct()` + `create_match_indirect()` |
| — | `update_match_initial_score()` |
| — | `mark_matches_quota_selected()` |
| — | `update_match_final_score()` |
| `get_matches_for_candidate()` | Rewritten — joins both tables, flattens to one dict |
| `update_match_outreach()` | Updated — now also writes `outreach_hook`, `email_subject`, `email_body` |

---

## 6. Key Architectural Decisions (with Reasons)

### Decision 1: Apify LinkedIn Post Scraper replaces Tavily for indirect channel
**Why:** Tavily's open-surface LinkedIn crawl returns near-zero real posts (LinkedIn blocks it). Apify Post Scraper returns structured objects: author name, author LinkedIn URL (reliable), post date, engagement, full text. Hiring manager identification is automatic — the post author IS the hiring manager.

### Decision 2: Two-phase scoring in Stage 4
**Why:** With ~50 total leads, running full Gemini scoring on all of them causes:
- High API cost
- Long wait times
- Context overflow leading to hallucinations
**Solution:** Fast Python pre-filter (Phase 1A, direct) + Gemini batch-of-5 (Phase 1B, indirect) → reduce to top 20 → full Gemini scoring on 20 only.

### Decision 3: Salary proximity dropped from Phase 1A scoring
**Why:** 85–90% of Indian startup job listings don't include salary. A 0.5 neutral score on null contributes nothing and adds noise. Salary data (if available) is a display enrichment only — not a ranking signal.

### Decision 4: Role aliases added to TargetRole in Stage 2
**Why:** "Chief of Staff" is also advertised as "Head of CEO Office", "CoS", "Strategy Lead". Without aliases, Stage 3 misses these listings entirely. Aliases serve two purposes:
- Feed Stage 3 Apify search queries (broader search coverage)
- Feed Phase 1A alias matching (identify alias-form job titles in results)

### Decision 5: Max 3 target roles from Stage 2
**Why:** More roles = more Apify queries = more leads = more API cost + longer processing. Three high-confidence roles give sufficient coverage without search space explosion.

### Decision 6: Single Apify actor call with multiple queries (not one call per query)
**Why:** Apify LinkedIn Jobs Scraper accepts a `queries` array — all search terms in one actor run. Multiple calls are unnecessary and wasteful. Pass primary titles + top 1 alias per role = 4–6 query strings in one call.

### Decision 7: Company stage enrichment added to Stage 3 for LinkedIn Jobs leads
**Why:** LinkedIn Jobs listings don't include funding stage. Stage 4's Phase 1A stage multiplier needs this data. Solution: After Apify LinkedIn scraping in Stage 3, run a batch Tavily lookup per unique company. Gemini extracts `company_stage_label`. Stage 4 receives pre-enriched data.

### Decision 8: Recency scoring now applies to BOTH LinkedIn Jobs AND Wellfound
**Why:** Wellfound actor confirmed to return `posted_at`. Previously, recency was dropped for Wellfound because it was assumed unavailable. Updated formula is now unified across all direct leads.

### Decision 9: outreach_hook generated in Stage 5, not Stage 4
**Why:** Previously computed during Stage 4 Gemini scoring (saves one LLM call in S5). But with the hybrid scoring redesign, Stage 4 Phase 2 is already prompting Gemini with a structured scoring schema. Adding outreach_hook to the same response overloads the prompt. Generated on-demand in Stage 5 instead — one clean Gemini call when the user clicks "Generate Intro".

### Decision 10: Quota system for final top 20
**Why:** Without a quota, formal Apify listings always dominate the top ranks (more data = better Python score). Hidden signals (the product's unique value) get buried. Quota guarantees both channels are represented.
- **Default quota:** Top 12 direct + Top 8 indirect
- **Edge case:** If indirect < 8, expand direct to fill 20 (17+3, 16+4, etc.)

### Decision 11: Final rank has no source distinction
**Why:** After quota selection, both channels go through identical Phase 2 Gemini scoring. The final ranked list is sorted purely by `final_score` — no source weighting. A founder's LinkedIn post that scores 88 ranks above a LinkedIn Job listing that scores 72.

---

## 7. Dependency: Stage 3 Must Be Updated Before Stage 4 Can Run

### The Problem
`pipeline/s3_discover.py` currently calls `upsert_job()` and `upsert_match()` — both removed from `queries.py` in the v2 rewrite. Stage 3 will crash at the persist step.

### The Fix — Targeted, Not a Full Rewrite
Only `_persist_leads()` function in `s3_discover.py` needs to change.

**Current (broken after v2):**
```python
def _persist_leads(candidate_id: str, leads: list[RawLead]):
    from db.queries import upsert_job, upsert_match
    for lead in leads:
        job_id = upsert_job(lead.model_dump())
        lead.job_id = job_id
        upsert_match(candidate_id, job_id, {"fit_score": 0.0, ...})
```

**Required replacement:**
```python
def _persist_leads(candidate_id: str, leads: list[RawLead]):
    from db.queries import upsert_direct_lead, upsert_indirect_lead
    from db.queries import create_match_direct, create_match_indirect
    for lead in leads:
        if lead.source_channel == "formal":
            lead_id = upsert_direct_lead(lead.model_dump())
            lead.job_id = lead_id
            create_match_direct(candidate_id, lead_id)
        else:
            lead_id = upsert_indirect_lead(lead.model_dump())
            lead.job_id = lead_id
            create_match_indirect(candidate_id, lead_id)
```

### Additional Stage 3 Changes (same session)

**Change A: Batch all queries in one Apify actor call**
```python
# BEFORE (one actor call per query — wasteful):
for query in queries:
    run = client.actor("...").call(run_input={"queries": [{"query": query}]})

# AFTER (one actor call with all queries):
all_query_objects = [{"query": q, "location": "India"} for q in queries]
run = client.actor("...").call(run_input={"queries": all_query_objects, "maxItems": 25})
```

**Change B: Company stage enrichment for LinkedIn Jobs leads**
After `_run_linkedin_jobs_channel()` returns leads, enrich `company_stage_label` for unique companies via Tavily:
```python
# New function to add in s3_discover.py:
def _enrich_company_stages(leads: list[RawLead]) -> list[RawLead]:
    """
    Tavily lookup for company stage for LinkedIn Jobs leads.
    Groups by unique company name to avoid duplicate searches.
    Updates company_stage_label on each lead in-place.
    Wellfound leads already have stage from actor data — skip them.
    """
```

**Change C: Switch indirect channel from Tavily to Apify LinkedIn Post Scraper**
Replace `_run_hidden_signals_channel()` which used Tavily with a new function that calls the Apify LinkedIn Post Scraper actor. The Gemini extraction step (batch of 8) that parsed Tavily snippets is replaced by direct structured data from Apify.

---

## 8. Stage 4 — Full Implementation Plan

### Entry Point
```python
def rank_leads(candidate_id: str, candidate_model: CandidateModel) -> list[ScoredLead]:
```
Reads direct and indirect match records from DB (persisted by Stage 3). Returns final ranked list of up to 20 `ScoredLead` objects. All scores written back to `matches` table.

### Phase 1A: Initial Scoring — Direct Leads (Pure Python)

**Scoring axes:**

| Axis | Formula | Notes |
|---|---|---|
| `stage_score` | Lookup table | `series_a/b=10, seed=8.5, series_c=7, pre_seed=6.5, late_stage=5, unknown=7` |
| `recency_score` | Decay on `posted_at` | ≤7d=10, 8–30d=8, 31–60d=6, >60d=4, null=5. **Both LinkedIn Jobs AND Wellfound** |
| `confidence_weight` | Alias match | rapidfuzz against all aliases of all target roles. high=1.0, medium=0.85, low=0.70, no match=0.60 |

**Hard filter first:** If `company_stage_label in ['mnc', 'public']` → `initial_score = 0`. Never reaches Phase 2. (Dealbreaker detection.)

**Formula (for leads that pass the filter):**
```
initial_score = (stage_score × 0.60 + recency_score × 0.40) × confidence_weight
```
Max = 10.0.

**Stage multiplier table:**
```python
STAGE_SCORE_TABLE = {
    'series_a':   10.0,
    'series_b':   10.0,
    'seed':        8.5,
    'series_c':    7.0,
    'pre_seed':    6.5,
    'late_stage':  5.0,
    'unknown':     7.0,   # Neutral — unknown is not disqualifying
    'public':      0.0,   # Disqualified via hard filter
    'mnc':         0.0,   # Disqualified via hard filter
}
```

**`initial_score_breakdown` JSONB structure:**
```json
{
  "stage": 10.0,
  "recency": 8.0,
  "confidence_weight": 0.85,
  "matched_role": "Chief of Staff",
  "matched_confidence": "medium",
  "stage_label": "series_a",
  "excluded_by_dealbreaker": false
}
```

### Phase 1B: Initial Scoring — Indirect Leads (Gemini, batches of 5)

**Gemini prompt:**
```
System: You are a recruitment relevance scorer. Score each LinkedIn post 0–100
on overall role fit and culture alignment against the candidate profile.
Score on intent and sector fit ONLY.
DO NOT penalize for missing salary, location, or any absent structured field.

Candidate target roles (with confidence):
- "Chief of Staff" (HIGH) — aliases: Head of CEO Office, CoS
- "EIR" (MEDIUM) — aliases: Entrepreneur in Residence

Candidate sector fit: [list]
Candidate seniority: [senior/mid/etc]

Posts (indexed 0–4): [snippets]

Return JSON: [{"post_index": 0, "score": 72, "reason": "one line"}]
```

**`initial_score_breakdown` for indirect:**
```json
{
  "gemini_scored": true,
  "reason": "Founder describes exactly a Chief of Staff need at a Series B startup"
}
```

### Quota Selection Logic
```python
valid_direct   = [m for m in direct_matches if m.initial_score > 0]
sorted_direct  = sorted(valid_direct, key=lambda x: x.initial_score, reverse=True)
sorted_indirect = sorted(indirect_matches, key=lambda x: x.initial_score, reverse=True)

available_indirect = min(len(sorted_indirect), 8)
direct_quota       = 20 - available_indirect   # Expands when indirect is scarce

selected = sorted_direct[:direct_quota] + sorted_indirect[:available_indirect]
mark_matches_quota_selected([m.match_id for m in selected])
```

### Phase 2: Final Gemini Scoring — Top 20 Only

**Same prompt for both direct and indirect leads (source type hidden from Gemini).**

**Batch size:** 5 leads per Gemini call (4 calls total for 20 leads).

**Prompt structure:**
```
System: You are an expert recruitment matching engine for Mesa School of Business.
Score each lead against the candidate on 5 axes (1.0–10.0 each).

RULES:
- HIGH CONFIDENCE target roles → stronger skills + experience scores when matched
- Penalise explicitly when role/company matches a dealbreaker
- Infer company_stage_label from description + company name if not provided

CANDIDATE:
Target roles:
  - "Chief of Staff" (HIGH) — also: Head of CEO Office, CoS, Office of the CEO
  - "EIR" (MEDIUM) — also: Entrepreneur in Residence, Venture Builder
Sector fit: [list]
Seniority: [senior]
Dealbreakers: ["MNC > 1000 employees", "Pure cold-calling sales roles"]
x_factor: [one sentence]
Compensation band: 33L–43L

LEADS (indexed 0–4):
[{company, role_title, description_excerpt, location, stage_label_if_known}]

Return JSON array:
[{
  "lead_index": 0,
  "axis_scores": {"skills": 8.5, "experience": 7.0, "domain": 9.0, "company_stage": 8.0, "location": 6.0},
  "rationale": "One punchy sentence — why this is or isn't a fit",
  "company_stage_label": "series_a"
}]
```

**Python computes `final_score` from Gemini's axis scores:**
```python
STAGE_MULTIPLIERS = {
    'series_a':  1.0,  'series_b':  1.0,
    'seed':      0.85, 'series_c':  0.85,
    'pre_seed':  0.70, 'late_stage': 0.60,
    'public':    0.50, 'mnc':       0.30,
    'unknown':   0.75,
}

AXIS_WEIGHTS = {
    'skills':        0.30,
    'experience':    0.25,
    'domain':        0.20,
    'company_stage': 0.15,
    'location':      0.10,
}

def compute_final_score(axis_scores: dict, stage_label: str) -> float:
    multiplier = STAGE_MULTIPLIERS.get(stage_label, 0.75)
    adjusted = {**axis_scores, 'company_stage': axis_scores['company_stage'] * multiplier}
    weighted = sum(adjusted[ax] * AXIS_WEIGHTS[ax] for ax in AXIS_WEIGHTS)
    return round(min(weighted * 10, 100), 2)
```

**Mesa benchmark flag:**
```python
FRESHER_BENCHMARK_LPA    = 22
NON_FRESHER_BENCHMARK_LPA = 35

def is_below_benchmark(salary_lpa: float | None, is_fresher: bool) -> bool:
    if salary_lpa is None:
        return False   # No data — don't flag
    floor = FRESHER_BENCHMARK_LPA if is_fresher else NON_FRESHER_BENCHMARK_LPA
    return salary_lpa < floor * 0.85
```

**Rank assignment:** Sort all 20 by `final_score` desc → assign `rank` 1–20. Write via `update_match_final_score()`.

### All Helper Functions for s4_rank.py

| Function | Signature | Purpose |
|---|---|---|
| `_score_direct_initial` | `(match, target_roles, dealbreakers) → (float, dict)` | Phase 1A — returns score + breakdown |
| `_stage_to_score` | `(label: str) → float` | Stage label → 0–10 |
| `_recency_score` | `(posted_at: datetime \| None) → float` | Decay curve |
| `_best_alias_match` | `(role_title, target_roles) → (weight, role, confidence)` | rapidfuzz across all aliases |
| `_score_indirect_batch` | `(batch, candidate_model) → list[(float, str)]` | Phase 1B — one Gemini call |
| `_select_quota` | `(direct, indirect) → list` | Quota with edge case |
| `_final_score_batch` | `(batch, candidate_model) → list[GeminiScoredJob]` | Phase 2 — one Gemini call per 5 |
| `compute_final_score` | `(axis_scores, stage_label) → float` | Weighted sum + multiplier |
| `is_below_benchmark` | `(salary_lpa, is_fresher) → bool` | Mesa floor check |
| `_assign_ranks` | `(scored_matches) → list` | Sort + assign rank 1–N |

---

## 9. Stage 5 — Outreach Drafts (Lazy, On Button Click)

### Entry Point
```python
def generate_outreach_on_demand(candidate_id: str, match_id: str) -> OutreachDraft:
```

### Flow
1. Fetch candidate, match, and the linked lead (direct or indirect) from Supabase
2. Call `find_email(hiring_manager_name, company_name)` — Apollo → Hunter fallback (already in `utils/enrichment.py`)
3. Run Gemini with outreach prompt → validates to `OutreachDraft` Pydantic model
4. **Also generate `outreach_hook` here** (not in Stage 4)
5. Write to DB via `update_match_outreach(match_id, {...})`
6. Return `OutreachDraft`

### Persona
Mesa Placement Director. Operator-to-operator tone. Zero fluff.

### Output (two variants always)
- **Full email:** 5–7 sentences. Structure: Context → Hook (outreach_hook) → 2 proof bullets → CTA (offer dossier)
- **LinkedIn DM:** 3 sentences max. End with yes/no question.
- **personalisation_note:** One thing careers team must verify before hitting send

### CTA Rule
Offer to send the student's dossier — NOT a generic calendar link. Founders ignore calendar asks.

### Apollo Limit
Apollo free tier = ~100 lookups/month. Hunter fallback = 50. Combined ~150. Covers 40 students × 3 leads.

### Cache Rule
Draft cached in `matches` table. Re-clicking "Generate Intro" returns cached version without re-running Apollo + Gemini.

---

## 10. Stage 6 — Internal Dossier (Lazy, On Button Click)

### Entry Point
```python
def generate_dossier(candidate_id: str, match_id: str) -> str:  # returns clean Markdown
```

### Triggered For
Rank = 1 lead only.

### Flow
1. Fetch candidate + rank-1 match + linked lead
2. Run 3 **parallel** Tavily searches:
   - `"{company}" startup funding stage investors India`
   - `"{company}" news announcement 2025 2026`
   - `"{manager_name}" {company} background founder linkedin`
3. Gemini dossier prompt → pure Markdown output
4. `strip_md_fences(raw_output)` — production fix (Gemini sometimes wraps in ` ```markdown ``` `)
5. Write to DB via `update_match_dossier(match_id, markdown)`
6. Return clean Markdown string (PDF bytes generated on-demand in `app.py`)

### 5 Required Sections (exact structure)
1. `## Company Snapshot` — stage, funding, team size, investors, HQ
2. `## Role Context` — why role exists now, success in 90 days
3. `## Why [Name] Fits — 3 Specific Reasons` — x_factor as lead reason
4. `## Likely Objections + How to Counter` — 2–3 objections with sharp counters
5. `## Competitive Landscape` — 2 sentences: who they're racing, why this hire is urgent

### Hallucination Prevention
Every company fact must be grounded in Tavily results. Gemini instructed: "If research doesn't confirm a fact, write 'Not confirmed publicly'".

### PDF Export
`markdown → HTML → PDF bytes` via `weasyprint`. Triggered by `st.download_button()` in `app.py`. Does NOT re-run Gemini.

---

## 11. Streamlit app.py — Full Dashboard Plan

### 5-Tab Layout

**Tab 1 — Run Pipeline**
- Radio: LinkedIn URL vs PDF Upload
- Input field / file uploader
- "Run Pipeline" button → calls S1 → S2 → S3 → S4 in sequence
- Live stage status badges update as each stage completes (`st.empty()`)
- `st.session_state`: stores `candidate_id`, `scored_leads`, `candidate_model`

**Tab 2 — Results (Ranked Leads)**
- Table of top 10 by `final_score`
- Columns: Rank, Company, Role, Score bar, Source type badge (Direct/Signal), Below-benchmark red badge
- "Show N more" expander for ranks 11–20
- Each row expandable → axis score breakdown + rationale

**Tab 3 — Outreach**
- Card per scored lead
- Button: "Generate Intro for {hiring_manager_name}" → calls S5 lazily
- 2-column layout: Email (left) + LinkedIn DM (right)
- `personalisation_note` shown as `st.info()` box
- Copy buttons for subject, body, DM

**Tab 4 — Dossier**
- Auto-targets rank=1 lead
- "Generate Dossier" button → calls S6 lazily
- `st.markdown()` renders 5-section memo
- "Download as PDF" → `st.download_button()` with weasyprint bytes

**Tab 5 — History**
- Supabase query: last 20 candidate runs
- Table: Name, Date, Top Role, Top Score, Pipeline Status
- Click row → reload that run into session state

---

## 12. Models That Need Updating

### `models/student.py` — TargetRole
```python
# CURRENT:
class TargetRole(BaseModel):
    title:      str
    confidence: ConfidenceLevel

# REQUIRED:
class TargetRole(BaseModel):
    title:      str
    confidence: ConfidenceLevel
    aliases:    list[str] = Field(
        default_factory=list,
        max_length=3,
        description="2–3 common alternative titles for this role. "
                    "E.g. 'Chief of Staff' → ['Head of CEO Office', 'CoS', 'Office of the CEO']. "
                    "Used for Stage 3 search query broadening and Phase 1A alias matching."
    )
```

Also cap `target_roles` in `CandidateModel`:
```python
target_roles: list[TargetRole] = Field(
    description="Top 2–3 best-fit roles ordered by confidence. MAX 3.",
    max_length=3,
)
```

### `models/leads.py` — GeminiScoredJob
```python
# REMOVE this field (outreach_hook is now Stage 5's responsibility):
outreach_hook: str = Field(...)

# GeminiScoredJob after removal:
class GeminiScoredJob(BaseModel):
    lead_index:          int           # Changed from job_id str — index-based for batch
    axis_scores:         AxisScores
    rationale:           str
    company_stage_label: str
```

---

## 13. Stage 2 Prompt Update Required

The `_S2_SYSTEM` prompt in `pipeline/s2_synthesise.py` must be updated to:
1. Cap at 3 target roles
2. Generate aliases per role

```
# Add to existing rules:
- target_roles: MAX 3 roles only. Pick the 3 highest-confidence fits.
- For each target role, generate 2-3 common alternative titles people actually use when posting
  jobs for the same function. These are 'aliases'. Be specific. Good: "Head of CEO Office".
  Bad: "Management Role".
- search_queries.formal_platforms: use the primary title for each role. Apify handles the search.
  Do NOT add aliases here — they are used internally for matching, not search strings.
```

---

## 14. Full Implementation Order (Execute in This Sequence)

### Batch 1: Model + Schema cleanup ✅ COMPLETE
- [x] `models/student.py` — Added `aliases: list[str]` to `TargetRole`, capped `target_roles` at 3
- [x] `models/leads.py` — Removed `outreach_hook` from `GeminiScoredJob`, changed `job_id` to `lead_index: int`
- [x] `pipeline/s2_synthesise.py` — Updated `_S2_SYSTEM` prompt (3 roles max, generate aliases, hidden_signals = post search phrases)
- [x] `db/queries.py` — Added `get_leads_for_ranking()` function

### Batch 2: Stage 3 fixes ✅ COMPLETE (full rewrite)
- [x] `pipeline/s3_discover.py` — Fixed `_persist_leads()` to use `upsert_direct_lead` / `upsert_indirect_lead` / `create_match_direct` / `create_match_indirect`
- [x] `pipeline/s3_discover.py` — Batched all Apify queries into ONE actor call per channel
- [x] `pipeline/s3_discover.py` — Replaced Tavily hidden signals with Apify LinkedIn Post Scraper (`_run_apify_post_scraper_channel`)
- [x] `pipeline/s3_discover.py` — Added `_enrich_company_stages()` with parallel Tavily lookups (ThreadPoolExecutor, max 5 concurrent)
- [x] `pipeline/s3_discover.py` — Wellfound now uses `posted_at` from actor data (recency applies to both sources)
- [x] `pipeline/s3_discover.py` — `discover_leads()` returns `(direct_leads, indirect_leads)` tuple

### Batch 3: Stage 4 — s4_rank.py ✅ COMPLETE
- [x] Phase 1A Python scorer (`_score_direct_initial`, `_recency_score`, `_best_alias_match`)
- [x] Phase 1B Gemini indirect scorer (`_score_indirect_initial_all`, batches of 5)
- [x] Quota selection (`_select_quota`, edge case handling)
- [x] Phase 2 Gemini final scorer (`_final_score_all`, batches of 5, `_FinalScoreBatch` schema)
- [x] `compute_final_score()` with weighted sum + stage multiplier
- [x] `is_below_benchmark()` — Mesa floor check
- [x] `_assign_ranks()` — sort + assign rank 1–N
- [x] `rank_leads()` entry point wired end-to-end

### Batch 4: Stage 5 — s5_outreach.py ✅ COMPLETE
- [x] `models/outreach.py` — Added `outreach_hook: str` and `hiring_manager_identified: bool` to `OutreachDraft`
- [x] `generate_outreach_on_demand(candidate_id, match_id)` — main entry point with cache check
- [x] Cache check — re-click returns cached draft instantly (no re-run of Apollo + Gemini)
- [x] `_get_company_news(company_name)` — Tavily lookup (3 results, 180 days), grounds email opening
- [x] Email enrichment — `find_email()` from `utils/enrichment.py` (Apollo → Hunter fallback)
- [x] `_call_gemini_outreach()` — single Gemini call returns outreach_hook + email + dm + personalisation_note
- [x] Null hiring manager handling — company-directed email + 300-char connection request note + actionable personalisation_note
- [x] `_infer_domain_from_url()` — extracts company domain from job post URL to help Hunter.io (skips job board URLs)
- [x] `_build_draft_from_cache()` — reconstructs OutreachDraft from cached DB fields
- [x] All fields written to matches table via `update_match_outreach()`

Key decisions baked in:
  - outreach_hook generated in SAME Gemini call as email/DM (Option A — one call, not two)
  - Null HM: skip Apollo/Hunter entirely, dm becomes LinkedIn connection request note (max 300 chars)
  - Tavily news snippet passed to Gemini as email context (not a separate summarisation call)
  - `hiring_manager_identified: bool` drives "Manual research needed" UI badge in app.py

### Batch 5: Stage 6 — s6_dossier.py ✅ COMPLETE
- [x] `generate_dossier(candidate_id, match_id) → DossierOutput` — rank-agnostic entry point
- [x] Cache check — re-click returns cached DossierOutput instantly (no Gemini re-run)
- [x] `_research_company()` — 3 parallel Tavily searches via ThreadPoolExecutor(max_workers=3)
      Adaptive search 3: manager background if HM known, company leadership if HM null
- [x] `_build_dossier_prompt()` — injects candidate, job, Stage 4 rationale, all Tavily results
      Injects Stage 5 `outreach_hook` as lead line of Reason 1 if already generated
- [x] `gemini_text()` call (not gemini_json) — natural Markdown output, no JSON wrapping
- [x] `strip_md_fences()` — removes ```markdown``` wrapping Gemini sometimes adds
- [x] `_validate_sections()` — checks all 5 headers present, retry once if any missing
- [x] `_append_footer()` — adds Mesa branding + date + Tavily source URLs
- [x] `update_match_dossier()` — caches final Markdown in Supabase
- [x] `export_dossier_pdf(markdown_content) → bytes` — standalone utility (weasyprint + markdown)
      Called from app.py on download button click. Does NOT re-run Gemini.
- [x] Returns `DossierOutput` (not plain str) — carries metadata + tavily_sources for footer

Key decisions baked in:
  - `gemini_text()` used (not `gemini_json()`) — dossier is a document, not structured data
  - Return type is `DossierOutput` — preserves tavily_sources + metadata for app.py
  - Null HM → search 3 becomes "{company} founders CEO leadership team India"
  - Section validation retries once with explicit missing-section list in prompt
  - `outreach_hook` from Stage 5 injected if available — keeps email and dossier consistent
  - `export_dossier_pdf()` lives in s6_dossier.py — imported by app.py, not embedded there

### Batch 6: Streamlit app.py ✅ COMPLETE
- [x] `db/queries.py` — Added `get_recent_candidates(limit=20)` for History view
- [x] State-machine architecture (not tabs) — `session_state.view` drives all navigation
      8 views: input → profiling → confirmed → searching → results → draft → dossier → history
- [x] **input view** — Animated gradient hero, dual-card input (LinkedIn URL / PDF), name field, Past Runs link
- [x] **profiling view** — S1+S2 pipeline with `st.status()` live progress, transitions to confirmed
- [x] **confirmed view** — Candidate snapshot card: target role tags, comp band, x_factor, dealbreaker chips
      "Start Job Search" button gives careers team chance to verify AI output before burning Apify credits
- [x] **searching view** — S3+S4 pipeline with `st.status()` live progress, transitions to results
- [x] **results view** — Ranked job cards via `st.expander()`, one per lead, sorted by final_score
      Each card: score bar (green/amber/red), source badge, stage badge, below-benchmark warning,
      axis score breakdown (5 mini-bars), rationale, hiring manager row, description excerpt
      Three buttons per card: Draft Email / LinkedIn Message / Dossier
- [x] **draft view** — Split screen `st.columns([1.15, 0.85])`
      Left: condensed job card with score, description, hiring manager
      Right: generated draft (email subject+body OR LinkedIn DM/connection request)
      Shows: email-found badge, manual-research warning, personalisation_note, outreach_hook expander
      Null HM → displays connection request note with character count
- [x] **dossier view** — Full-width: Mesa header, markdown rendered as HTML inside a card, PDF download button (top + bottom)
- [x] **history view** — Past 20 runs from Supabase, each row: name, input type badge, date, top role, status icon, Load button
      Load reconstructs session state from DB: candidate_id + scored_leads, jumps to results view
- [x] Custom CSS: animated gradient hero, card shadows, score bar colors, badges, axis bars, Mesa coral (#E8521A) primary, fadeIn animations

Key design decisions:
  - State-machine (not tabs) — feels like a product, not a script
  - Candidate snapshot shown before job search — careers team verifies AI output first
  - History "Load" normalises match dicts (adds job_id = id) so S5/S6 work identically for fresh + past runs
  - All pipeline calls wrapped in try/except — errors shown inline, user can retry or start over
  - Dossier markdown converted via `markdown` package before rendering (not raw markdown in HTML)

---

## 15. Environment Variables Required

```env
# LLM
GEMINI_API_KEY=

# Profile scraping
PROXYCURL_API_KEY=

# Job discovery
APIFY_API_KEY=

# Web search
TAVILY_API_KEY=

# Email enrichment
APOLLO_API_KEY=
HUNTER_API_KEY=

# Database
SUPABASE_URL=
SUPABASE_KEY=

# Scoring thresholds (can override defaults)
MIN_FIT_SCORE=55
FRESHER_BENCHMARK_LPA=22
NON_FRESHER_BENCHMARK_LPA=35
MAX_RAW_LEADS=50
DIRECT_QUOTA=12
INDIRECT_QUOTA=8
GEMINI_MODEL=gemini-2.5-flash
```

---

## 16. Supabase Migration Note

If running against an existing v1 database, run these drops BEFORE executing `schema.sql`:

```sql
DROP TABLE IF EXISTS matches CASCADE;
DROP TABLE IF EXISTS jobs    CASCADE;
```

Then run the full `schema.sql`. The `candidates` table is backward-compatible (only new `pipeline_status` values added).

---

*Last updated: May 2026 | v2 Architecture | Gemini 2.5 Flash*
