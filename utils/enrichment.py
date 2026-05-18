"""
Email enrichment: Apollo.io (primary) → Hunter.io (fallback).

Strategy:
    1. Try Apollo.io people/match endpoint by name + company.
    2. If Apollo returns no verified email, try Hunter.io email-finder.
    3. If both fail, return None — UI shows 'use LinkedIn DM' badge.

Free tier limits (as of 2026):
    Apollo:  ~100 email lookups/month (personal email signup)
    Hunter:  50 credits/month
    Combined: ~150/month — covers 40 students × 3 leads = 120 lookups
"""
import os
import logging
import requests
from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_exponential

load_dotenv()

log = logging.getLogger(__name__)

APOLLO_API_KEY    = os.getenv("APOLLO_API_KEY", "")
HUNTER_API_KEY    = os.getenv("HUNTER_API_KEY", "")
SNOV_CLIENT_ID    = os.getenv("SNOV_CLIENT_ID", "")
SNOV_CLIENT_SECRET = os.getenv("SNOV_CLIENT_SECRET", "")


def find_email(
    name: str,
    company: str,
    domain: str | None = None,
    linkedin_url: str | None = None,
) -> str | None:
    """
    Find a hiring manager's email via Apollo → Hunter fallback chain.

    Args:
        name:         Full name of the hiring manager
        company:      Company name (used for Apollo lookup)
        domain:       Optional company domain e.g. 'acme.com' (helps Hunter)
        linkedin_url: Hiring manager's LinkedIn profile URL — significantly improves Apollo accuracy

    Returns:
        Verified email address or None if not found
    """
    # Try Apollo first (LinkedIn URL gives much better match accuracy)
    email = _apollo_lookup(name, company, linkedin_url=linkedin_url)
    if email:
        log.debug("find_email: Apollo hit for %s @ %s → %s", name, company, email)
        return email

    # Snov.io fallback — better India coverage, accepts LinkedIn URL directly
    email = _snov_lookup(name, company, linkedin_url=linkedin_url)
    if email:
        log.debug("find_email: Snov.io hit for %s @ %s → %s", name, company, email)
        return email

    # Hunter last resort — needs domain
    if not domain:
        domain = _infer_domain(company)

    if domain:
        email = _hunter_lookup(name, domain)
        if email:
            log.debug("find_email: Hunter hit for %s @ %s → %s", name, domain, email)
            return email

    log.info("find_email: no email found for %s @ %s — will use LinkedIn DM", name, company)
    return None


@retry(stop=stop_after_attempt(2), wait=wait_exponential(min=1, max=5), reraise=False)
def _apollo_lookup(name: str, company: str, linkedin_url: str | None = None) -> str | None:
    """
    Apollo.io People Match API — finds email by name + company.
    Passing linkedin_url dramatically improves match accuracy over name-only lookup.
    """
    if not APOLLO_API_KEY:
        return None

    try:
        parts = name.strip().split()
        first_name = parts[0] if parts else name
        last_name = " ".join(parts[1:]) if len(parts) > 1 else ""

        payload: dict = {
            "api_key":                APOLLO_API_KEY,
            "first_name":             first_name,
            "last_name":              last_name,
            "organization_name":      company,
            "reveal_personal_emails": True,   # include personal emails on free tier
        }
        if linkedin_url:
            payload["linkedin_url"] = linkedin_url   # anchor lookup to exact profile

        response = requests.post(
            "https://api.apollo.io/api/v1/people/match",
            headers={"Content-Type": "application/json", "Cache-Control": "no-cache"},
            json=payload,
            timeout=10,
        )
        if response.status_code == 200:
            data = response.json()
            person = data.get("person", {})
            # Primary email field
            email = person.get("email")
            if email and "@" in email:
                return email
            # Free tier often nulls person.email but populates person.emails[]
            for entry in person.get("emails", []):
                addr = entry.get("email") if isinstance(entry, dict) else entry
                if addr and "@" in addr:
                    return addr
    except Exception as e:
        log.warning("Apollo lookup failed for %s: %s", name, e)
    return None


def _snov_get_token() -> str | None:
    """Exchange Snov.io client credentials for an OAuth access token."""
    if not SNOV_CLIENT_ID or not SNOV_CLIENT_SECRET:
        return None
    try:
        resp = requests.post(
            "https://api.snov.io/v1/oauth/access_token",
            data={
                "grant_type":    "client_credentials",
                "client_id":     SNOV_CLIENT_ID,
                "client_secret": SNOV_CLIENT_SECRET,
            },
            timeout=10,
        )
        if resp.status_code == 200:
            return resp.json().get("access_token")
    except Exception as e:
        log.warning("Snov.io token fetch failed: %s", e)
    return None


@retry(stop=stop_after_attempt(2), wait=wait_exponential(min=1, max=5), reraise=False)
def _snov_lookup(
    name: str,
    company: str,
    linkedin_url: str | None = None,
) -> str | None:
    """
    Snov.io email finder — better India coverage than Apollo/Hunter.

    Strategy:
        1. If linkedin_url present → use LinkedIn profile-based lookup (most accurate).
        2. Fallback → name + inferred domain lookup.
    """
    if not SNOV_CLIENT_ID or not SNOV_CLIENT_SECRET:
        return None

    token = _snov_get_token()
    if not token:
        return None

    try:
        # ── Path 1: LinkedIn URL lookup ───────────────────────────────────────
        if linkedin_url:
            # Step A: queue the LinkedIn URL for email extraction
            add_resp = requests.post(
                "https://api.snov.io/v1/add-url-for-search",
                data={"access_token": token, "url": linkedin_url},
                timeout=10,
            )
            if add_resp.status_code == 200:
                # Step B: retrieve emails for that URL
                get_resp = requests.post(
                    "https://api.snov.io/v1/get-emails-from-url",
                    data={"access_token": token, "url": linkedin_url},
                    timeout=15,
                )
                if get_resp.status_code == 200:
                    for entry in get_resp.json().get("emails", []):
                        addr = entry.get("email") if isinstance(entry, dict) else entry
                        if addr and "@" in addr:
                            return addr

        # ── Path 2: Name + domain lookup ─────────────────────────────────────
        domain = _infer_domain(company)
        if not domain:
            return None

        parts      = name.strip().split()
        first_name = parts[0] if parts else name
        last_name  = " ".join(parts[1:]) if len(parts) > 1 else ""

        find_resp = requests.post(
            "https://api.snov.io/v1/get-emails-from-names",
            data={
                "access_token": token,
                "domain":       domain,
                "firstName":    first_name,
                "lastName":     last_name,
                "type":         "personal",
            },
            timeout=10,
        )
        if find_resp.status_code == 200:
            for entry in find_resp.json().get("emails", []):
                addr = entry.get("email") if isinstance(entry, dict) else entry
                if addr and "@" in addr:
                    return addr

    except Exception as e:
        log.warning("Snov.io lookup failed for %s @ %s: %s", name, company, e)

    return None


@retry(stop=stop_after_attempt(2), wait=wait_exponential(min=1, max=5), reraise=False)
def _hunter_lookup(name: str, domain: str) -> str | None:
    """Hunter.io Email Finder API — finds email by name + company domain."""
    if not HUNTER_API_KEY:
        return None

    try:
        parts = name.strip().split()
        first_name = parts[0] if parts else name
        last_name = " ".join(parts[1:]) if len(parts) > 1 else ""

        response = requests.get(
            "https://api.hunter.io/v2/email-finder",
            params={
                "domain":     domain,
                "first_name": first_name,
                "last_name":  last_name,
                "api_key":    HUNTER_API_KEY,
            },
            timeout=10,
        )
        if response.status_code == 200:
            data = response.json()
            email = data.get("data", {}).get("email")
            if email and "@" in email:
                return email
    except Exception as e:
        log.warning("Hunter lookup failed for %s @ %s: %s", name, domain, e)
    return None


def _infer_domain(company: str) -> str | None:
    """
    Best-effort domain inference from company name.
    E.g. 'Acme Technologies' → 'acmetechnologies.com' (rough guess).
    In practice, Gemini extracts the domain from job post URLs in S3.
    """
    import re
    clean = re.sub(r"[^a-z0-9]", "", company.lower())
    if clean:
        return f"{clean}.com"
    return None
