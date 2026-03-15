"""
app/core/database.py
Supabase client — service role for backend operations, anon for user-scoped calls.
"""
from functools import lru_cache

from supabase import Client, create_client

from app.core.config import get_settings


@lru_cache
def get_supabase() -> Client:
    """Service-role client for backend workers and admin operations."""
    s = get_settings()
    return create_client(s.supabase_url, s.supabase_service_role_key)


@lru_cache
def get_supabase_anon() -> Client:
    """Anon-key client — respects Row Level Security for user-facing endpoints."""
    s = get_settings()
    return create_client(s.supabase_url, s.supabase_anon_key)
