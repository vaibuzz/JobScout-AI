# JobScout AI — Agentic Career Intelligence Pipeline

> **An end-to-end, multi-stage AI agent** that takes a LinkedIn profile or resume PDF and autonomously discovers ranked job leads, generates personalised outreach drafts, and produces an internal placement dossier — all in under 5 minutes.

[![Live Demo](https://img.shields.io/badge/Live%20Demo-Streamlit%20Cloud-FF4B4B?style=for-the-badge&logo=streamlit)](https://jobscout-ai.streamlit.app)
[![Python](https://img.shields.io/badge/Python-3.11-3776AB?style=for-the-badge&logo=python)](https://python.org)
[![Gemini](https://img.shields.io/badge/LLM-Gemini%202.5-4285F4?style=for-the-badge&logo=google)](https://aistudio.google.com)
[![Supabase](https://img.shields.io/badge/Database-Supabase-3ECF8E?style=for-the-badge&logo=supabase)](https://supabase.com)

---

## What Is This?

JobScout AI is a **6-stage agentic pipeline** built for high-stakes placement operations. Unlike traditional job boards or simple search tools, JobScout operates as an autonomous agent:

- **No manual job searching** — it queries LinkedIn Jobs, Wellfound, and founder LinkedIn posts simultaneously
- **No manual scoring** — a two-phase hybrid scoring engine (Python + Gemini) ranks every lead
- **No manual outreach writing** — personalised emails and LinkedIn DMs are generated on demand
- **No manual research** — a 5-section internal dossier is auto-generated with real web intelligence

The system is designed around the principle that **the best jobs are never posted publicly** — it finds both formal listings and hidden hiring signals from founder social activity.

---

## Architecture — 6-Stage Agentic Pipeline

```
Input: LinkedIn URL / Resume PDF
           │
           ▼
┌─────────────────────┐
│  S1 — INGEST        │  Apify LinkedIn Scraper OR pdfplumber + Gemini
│  Profile Extraction │  → Normalised StudentProfile (Pydantic)
└────────┬────────────┘
         │
         ▼
┌─────────────────────┐
│  S2 — SYNTHESISE    │  Gemini 2.5 Pro (thinking mode)
│  Candidate Model    │  → 3 target roles + aliases, sector fit,
│                     │    comp band (Tavily-grounded), dealbreakers,
│                     │    x_factor, Apify search queries
└────────┬────────────┘
         │
         ▼
┌─────────────────────┐
│  S3 — DISCOVER      │  Parallel Apify actor calls:
│  Lead Discovery     │  • LinkedIn Jobs Scraper (formal listings)
│                     │  • Wellfound Scraper (startup listings)
│                     │  • LinkedIn Post Scraper (founder hiring signals)
│                     │  + Tavily company stage enrichment
│                     │  → direct_leads + indirect_leads → Supabase
└────────┬────────────┘
         │
         ▼
┌─────────────────────┐
│  S4 — RANK          │  Two-phase hybrid scoring:
│  Scoring Engine     │  Phase 1A: Python scorer (stage × recency × alias match)
│                     │  Phase 1B: Gemini batch scorer for indirect leads
│                     │  Quota: Top 12 direct + Top 8 indirect = 20 leads
│                     │  Phase 2: Gemini 5-axis deep scorer → final rank
└────────┬────────────┘
         │
         ▼
┌─────────────────────┐
│  S5 — OUTREACH      │  On-demand (per lead, per click):
│  Draft Generation   │  Apollo → Snov.io → Hunter (email waterfall)
│                     │  Gemini: email + LinkedIn DM + outreach_hook
│                     │  Cached in Supabase after first generation
└────────┬────────────┘
         │
         ▼
┌─────────────────────┐
│  S6 — DOSSIER       │  On-demand (rank-1 lead only):
│  Internal Memo      │  3 parallel Tavily searches (funding, news, founder)
│                     │  Gemini: 5-section placement memo in Markdown
│                     │  PDF export via fpdf2
└────────┬────────────┘
         │
         ▼
Output: Ranked leads + Outreach drafts + PDF Dossier
```

---

## Key Technical Decisions

| Decision | Rationale |
|---|---|
| **Two-table DB schema** (`direct_leads` + `indirect_leads`) | Formal and social data have fundamentally different fields — mixing into one table caused nulls everywhere |
| **Quota system (12 direct + 8 indirect)** | Without a quota, structured Apify listings always dominate rankings and bury the unique hidden signals |
| **Apify Post Scraper for indirect channel** | Tavily's open-surface LinkedIn crawl returns near-zero real posts — Apify returns structured objects with author info |
| **Gemini thinking mode for S2** | Synthesis requires multi-step reasoning (pivot detection, evidence gating, market demand checks) |
| **Two-phase scoring** | Running full Gemini scoring on 50 leads = high cost + context overflow. Pre-filter to 20, then deep-score |
| **`outreach_hook` generated in S5, not S4** | S4 Gemini prompt already complex — adding outreach to same call overloads context and degrades quality |
| **Lazy S5/S6 generation** | Apollo + Gemini calls are expensive — only run when the user clicks "Generate", cached afterwards |

---

## Tech Stack

| Layer | Tool | Purpose |
|---|---|---|
| **LLM** | Gemini 2.5 Flash / Pro | All synthesis, scoring, outreach, dossier generation |
| **UI** | Streamlit | State-machine dashboard (8 views) |
| **Profile (URL)** | Apify LinkedIn Scraper | LinkedIn profile data extraction |
| **Profile (PDF)** | pdfplumber + Gemini | Resume parsing fallback |
| **Formal jobs** | Apify LinkedIn Jobs + Wellfound | Structured job listings |
| **Hiring signals** | Apify LinkedIn Post Scraper | Founder hiring posts (indirect channel) |
| **Web search** | Tavily | Comp grounding, company enrichment, dossier research |
| **Email lookup** | Apollo → Snov.io → Hunter | Triple-waterfall email enrichment |
| **Database** | Supabase (Postgres) | Persistent storage for all pipeline outputs |
| **Validation** | Pydantic v2 | Every Gemini JSON response is validated |
| **Deduplication** | rapidfuzz | Cross-channel fuzzy duplicate detection |
| **PDF export** | fpdf2 | Pure-Python PDF (no system deps) |
| **Retry logic** | tenacity | All external API calls with exponential backoff |

---

## Pipeline Output — What You Get Per Candidate

```
┌─────────────────────────────────────────────────┐
│  Candidate Snapshot                             │
│  ├── 3 target roles (with confidence levels)    │
│  ├── Sector fit tags                            │
│  ├── Compensation band (market-grounded)        │
│  ├── Dealbreakers                               │
│  └── X-factor (unique differentiator)          │
├─────────────────────────────────────────────────┤
│  Top 20 Ranked Job Leads                        │
│  ├── Final score (0–100)                        │
│  ├── 5-axis breakdown (skills/exp/domain/       │
│  │   stage/location)                            │
│  ├── Source badge (Direct / Signal)             │
│  ├── Hiring manager details                     │
│  └── Below-benchmark salary warning             │
├─────────────────────────────────────────────────┤
│  Per-Lead Outreach (on click)                   │
│  ├── Full email (subject + body)                │
│  ├── LinkedIn DM / connection request           │
│  ├── Outreach hook                              │
│  └── Personalisation note for careers team      │
├─────────────────────────────────────────────────┤
│  Internal Dossier — Rank #1 Lead (on click)     │
│  ├── Company Snapshot                           │
│  ├── Role Context                               │
│  ├── Why [Candidate] Fits — 3 Specific Reasons  │
│  ├── Likely Objections + Counters               │
│  ├── Competitive Landscape                      │
│  └── PDF download                              │
└─────────────────────────────────────────────────┘
```

---

## Quick Start (Local)

### 1. Clone & install
```bash
git clone https://github.com/vaibuzz/JobScout-AI.git
cd JobScout-AI
pip install -r requirements.txt
```

### 2. Set up environment
```bash
cp .env.example .env
# Fill in your API keys — see .env.example for all required keys
```

### 3. Set up Supabase
```bash
# 1. Create a free project at https://supabase.com
# 2. Open the SQL editor and run the contents of db/schema.sql
# 3. Copy your project URL and anon key into .env
```

### 4. Run the dashboard
```bash
streamlit run app.py
```

### 5. Docker (alternative)
```bash
docker-compose up
```

---

## Environment Variables

| Variable | Service | Get it at |
|---|---|---|
| `GEMINI_API_KEY` | Google Gemini | [aistudio.google.com](https://aistudio.google.com/app/apikey) |
| `APIFY_API_KEY` | Apify | [apify.com](https://apify.com) — $5 free credits |
| `TAVILY_API_KEY` | Tavily | [tavily.com](https://tavily.com) — 1,000 free/month |
| `APOLLO_API_KEY` | Apollo.io | [apollo.io](https://apollo.io) — 100 free/month |
| `SNOV_CLIENT_ID` + `SNOV_CLIENT_SECRET` | Snov.io | [app.snov.io](https://app.snov.io) — 50 free/month |
| `HUNTER_API_KEY` | Hunter.io | [hunter.io](https://hunter.io) — 25 free/month |
| `SUPABASE_URL` + `SUPABASE_KEY` | Supabase | [supabase.com](https://supabase.com) — free tier |

---

## Database Schema

Two-table architecture (v2) in Supabase:

```
candidates       — one row per pipeline run
direct_leads     — structured listings (LinkedIn Jobs, Wellfound)
indirect_leads   — unstructured posts (LinkedIn Post Scraper)
matches          — links candidate ↔ lead with two-phase scores + outreach cache
```

Run `db/schema.sql` in the Supabase SQL editor to initialise.

---

## Project Structure

```
JobScout-AI/
├── app.py                    ← Streamlit dashboard (state-machine, 8 views)
├── requirements.txt
├── .env.example
├── Dockerfile / docker-compose.yml
│
├── pipeline/
│   ├── s1_ingest.py          ← Profile ingestion (URL or PDF)
│   ├── s2_synthesise.py      ← Candidate model synthesis (Gemini Pro)
│   ├── s3_discover.py        ← Lead discovery (Apify actors + Tavily)
│   ├── s4_rank.py            ← Two-phase scoring + ranking
│   ├── s5_outreach.py        ← Outreach draft generation (lazy)
│   └── s6_dossier.py         ← Internal dossier generation (lazy)
│
├── models/
│   ├── student.py            ← StudentProfile + CandidateModel schemas
│   ├── leads.py              ← RawLead + GeminiScoredJob schemas
│   └── outreach.py           ← OutreachDraft + DossierOutput schemas
│
├── db/
│   ├── schema.sql            ← Supabase v2 schema (two-table architecture)
│   ├── queries.py            ← All DB read/write functions
│   └── client.py             ← Supabase singleton client
│
└── utils/
    ├── gemini.py             ← Gemini wrapper (gemini_json, gemini_text)
    ├── search.py             ← Tavily search wrapper
    ├── enrichment.py         ← Email waterfall (Apollo → Snov.io → Hunter)
    └── helpers.py            ← Dedup, chunking utilities
```

---

*Built with Gemini 2.5 Flash/Pro · Apify · Tavily · Supabase · Streamlit*
