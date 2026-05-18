"""
Search helpers for Mesa Careers AI.

Tavily open-surface search replaces both Tavily-with-site-filter AND Twitter API v2.
Tavily already indexes LinkedIn posts, Twitter/X, Wellfound, Reddit simultaneously
in one call — no separate Twitter API needed (which is pay-per-use as of Feb 2026).

Wellfound fallback: Use Tavily with site:wellfound.com filter instead of nonexistent API.

Public API:
    tavily_search(query, max_results, days_back) -> list[dict]
    wellfound_fallback(queries) -> list[dict]
"""
import os
import logging
from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_exponential

load_dotenv()

log = logging.getLogger(__name__)

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")
HIDDEN_SIGNAL_DAYS = int(os.getenv("HIDDEN_SIGNAL_DAYS", "14"))


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8), reraise=True)
def tavily_search(
    query: str,
    max_results: int = 8,
    days_back: int = HIDDEN_SIGNAL_DAYS,
) -> list[dict]:
    """
    Search the open web using Tavily API.
    Open-surface search (no site: filter) hits LinkedIn, Twitter/X,
    Wellfound, Reddit, and news sources simultaneously.

    Args:
        query:       Search query string (Boolean syntax supported)
        max_results: Max number of results to return (default 8)
        days_back:   Only return results from last N days (default 14)

    Returns:
        List of dicts with keys: title, url, content, published_date, score
    """
    if not TAVILY_API_KEY:
        log.warning("TAVILY_API_KEY not set — returning empty results")
        return []

    from tavily import TavilyClient
    client = TavilyClient(api_key=TAVILY_API_KEY)

    try:
        response = client.search(
            query=query,
            max_results=max_results,
            search_depth="basic",
            include_answer=False,
            days=days_back,
        )
        results = response.get("results", [])
        log.debug("tavily_search: %d results for '%s'", len(results), query[:60])
        return [
            {
                "title":          r.get("title", ""),
                "url":            r.get("url", ""),
                "content":        r.get("content", ""),
                "published_date": r.get("published_date"),
                "score":          r.get("score", 0.0),
            }
            for r in results
        ]
    except Exception as e:
        log.error("tavily_search failed for query '%s': %s", query[:60], e)
        raise


def wellfound_fallback(queries: list[str]) -> list[dict]:
    """
    Wellfound job search via Tavily site filter.
    Used when Apify LinkedIn Jobs returns <5 results.
    (Wellfound has no public API — Tavily site filter is the correct approach.)

    Args:
        queries: List of Boolean/keyword search strings from Stage 2

    Returns:
        List of raw Tavily result dicts
    """
    results = []
    for q in queries:
        # Search Wellfound specifically for startup job listings
        wellfound_query = f"({q}) site:wellfound.com"
        hits = tavily_search(wellfound_query, max_results=5, days_back=30)
        results.extend(hits)
        log.debug("wellfound_fallback: %d results for query '%s'", len(hits), q[:50])
    return results
