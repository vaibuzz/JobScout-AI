"""
Supabase client singleton.
Loads SUPABASE_URL and SUPABASE_KEY from environment variables.
TODO: Implement in Step 2
"""
import os
from dotenv import load_dotenv

load_dotenv()

# Lazy singleton — import and call get_client() wherever needed
_client = None


def get_client():
    """Return the Supabase client, initialising it on first call."""
    global _client
    if _client is None:
        from supabase import create_client
        url = os.environ["SUPABASE_URL"]
        key = os.environ["SUPABASE_KEY"]
        _client = create_client(url, key)
    return _client
