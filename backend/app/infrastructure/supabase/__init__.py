"""Supabase integration — auth, storage, realtime, REST.

Repositories that need raw SQL throughput use SQLAlchemy/asyncpg pointed at
the Supabase Postgres connection string (`SupabaseSettings.postgres_connection_string`).
"""
from .client import SupabaseClientFactory, get_supabase_client
from .auth import SupabaseAuth
from .storage import SupabaseStorage
from .realtime import SupabaseRealtime

__all__ = [
    "SupabaseClientFactory", "get_supabase_client",
    "SupabaseAuth", "SupabaseStorage", "SupabaseRealtime",
]
