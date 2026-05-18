# Mesa Careers AI: Pipeline Architecture & Data Flow Decisions

This document summarizes the agreed-upon architecture for **Stage 3 (Discovery)** and **Stage 4 (Ranking)** of the pipeline, focusing on how data flows, how it is persisted, and how the scoring engine ensures fair, hallucination-free ranking.

## 1. Data Flow & Storage Approach (Two Distinct Tables)

To avoid comparing apples to oranges and to handle structured vs. unstructured data properly, we split the lead generation into two distinct channels, each with its own database table:

### Table A: `direct_leads`
*   **Source:** Apify LinkedIn Jobs Scraper & Apify Wellfound Scraper.
*   **Data Type:** Highly structured, complete, and trusted.
*   **Fields:** `job_id`, `company_name`, `role_title`, `description`, `salary_estimate`, `posted_at`, `location`, `remote_ok`.
*   **Flow:** Apify JSON → Pydantic `RawLead` → Saved to `direct_leads` table.

### Table B: `indirect_leads`
*   **Source:** Tavily Open-Surface Web Search (Twitter/X, LinkedIn Posts, Reddit).
*   **Data Type:** Unstructured snippets, partially inferred, flagged as unverified.
*   **Fields:** `signal_url`, `company_name`, `role_title`, `snippet`, `hiring_manager_name`, `hiring_manager_linkedin` (inferred), `inferred_posted_at`, `platform`.
*   **Flow:** Tavily Snippet → Gemini extraction (batch of 8) → Pydantic `RawLead` (flagged unverified) → Saved to `indirect_leads` table.

## 2. Scoring Engine Strategy (Hybrid Approach)

We cannot use the exact same scoring logic for both types of leads because `indirect_leads` (e.g., a founder's tweet) will almost always lack explicit salary or location data, and would be unfairly penalized against formal job listings. 

Therefore, we use a **hybrid scoring engine**:

### For `direct_leads` (Apify): Pure Python Deterministic Scoring
Because the data is structured, we use a fast, cost-effective Python-based scoring engine.
*   **Logic:** Math-based weighted sum.
*   **Axes:** Role string matching (Rapidfuzz), salary proximity to candidate's band, recency multiplier (newer jobs score higher), stage match.
*   **LLM Usage:** NONE for the base score. (100% deterministic).

### For `indirect_leads` (Tavily): Gemini LLM Contextual Scoring
Because the data is unstructured (e.g., "Looking for a killer chief of staff to help me scale"), standard math matching fails. We need Gemini to understand the *intent*.
*   **Logic:** Gemini evaluates the snippet against the `CandidateModel`.
*   **Axes:** Gemini is explicitly prompted to score based on **role intent and culture fit**, and instructed **NOT to penalize for missing salary/location**.
*   **LLM Usage:** 1 Gemini API call per lead.

## 3. Gemini Interaction & Hallucination Prevention

*   **No Bulk LLM Calls:** We **never** send the entire list of 50+ leads to Gemini at once. Bulk processing overflows the context window and causes hallucinations where the LLM invents salaries or managers that don't exist.
*   **Individual Processing:** For `indirect_leads`, we send leads to Gemini either individually or in very small, strict batches (e.g., 5 at a time) to score them.
*   **On-Demand Enrichment:** For `direct_leads`, if the UI needs a "Why this is a good fit" rationale, we only ask Gemini to generate that rationale for the **Top K** leads that the user actually sees, saving API costs.

## 4. Final Ranking & Quota System

To ensure that the "Hidden Signals" (the main attraction of this product) aren't buried by formal listings, the final Stage 4 output uses a **Quota System**.

Instead of a single raw sorted list, the final Top 20 presented to the user is composed of:
*   **Top 12** highest-scoring from `direct_leads`
*   **Top 8** highest-scoring from `indirect_leads`

This guarantees the user sees high-quality formal jobs alongside high-value, under-the-radar founder signals.
