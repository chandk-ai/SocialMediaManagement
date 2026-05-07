"""Generic SQL database source — runs a parametrised SELECT and yields rows
as `SourceItem`s. Works with Postgres, MySQL, MSSQL, SQLite, etc., via
SQLAlchemy URLs.

Safety: only one statement per query; the adapter rejects multi-statement
inputs and refuses anything that isn't a SELECT.
"""
from __future__ import annotations

from datetime import datetime
from typing import AsyncIterator

from app.core.logging import get_logger
from app.domain.entities.source import SourceItem
from app.plugins.registry import register_plugin

from .base import ContentSource, SourceConnectionError

log = get_logger(__name__)


@register_plugin("source", "sql_database", api_version="1.0")
class SQLDatabaseSource(ContentSource):
    display_name = "SQL database"
    description = "Run a SELECT query against any SQLAlchemy-supported DB."
    config_schema = {
        "type": "object",
        "required": ["url", "query"],
        "properties": {
            "url":      {"type": "string", "title": "SQLAlchemy URL",
                         "examples": [
                             "postgresql+psycopg://user:pass@host/db",
                             "mysql+pymysql://user:pass@host/db",
                             "sqlite:///./local.db",
                             "mssql+pyodbc://...",
                         ]},
            "query":    {"type": "string", "title": "SELECT query (single statement)"},
            "title_column":     {"type": "string", "default": "title"},
            "body_column":      {"type": "string", "default": "body"},
            "id_column":        {"type": "string", "default": "id"},
            "date_column":      {"type": "string", "default": "updated_at"},
            "limit":    {"type": "integer", "minimum": 1, "maximum": 10000, "default": 200},
        },
    }

    async def connect(self) -> None:
        q = (self.config.get("query") or "").strip().rstrip(";")
        if ";" in q:
            raise SourceConnectionError("only one statement per query is allowed")
        if not q.lower().startswith("select"):
            raise SourceConnectionError("only SELECT queries are allowed")
        try:
            from sqlalchemy import create_engine, text
        except ImportError as exc:                                # pragma: no cover
            raise SourceConnectionError(
                "SQLAlchemy is not installed on the backend.",
            ) from exc
        # Live ping — proves URL + credentials + driver are usable.
        try:
            engine = create_engine(self.config["url"], pool_pre_ping=True)
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        except Exception as exc:                                  # noqa: BLE001
            raise SourceConnectionError(str(exc)) from exc

    async def fetch(self, since: datetime | None = None) -> AsyncIterator[SourceItem]:
        from sqlalchemy import create_engine, text                # imported in connect()
        engine = create_engine(self.config["url"], pool_pre_ping=True)
        title_col = self.config.get("title_column", "title")
        body_col = self.config.get("body_column", "body")
        id_col = self.config.get("id_column", "id")
        date_col = self.config.get("date_column", "updated_at")
        limit = int(self.config.get("limit", 200))
        query = self.config["query"].strip().rstrip(";") + f"\nLIMIT {limit}"
        with engine.connect() as conn:
            for row in conn.execute(text(query)).mappings():
                modified = row.get(date_col)
                if since and isinstance(modified, datetime) and modified <= since:
                    continue
                yield SourceItem(
                    external_id=str(row.get(id_col) or row.get(title_col) or "row"),
                    title=str(row.get(title_col, "")),
                    body=str(row.get(body_col, "")),
                    url=None,
                    published_at=modified if isinstance(modified, datetime) else None,
                    metadata={k: v for k, v in row.items()
                              if k not in {title_col, body_col, id_col, date_col}},
                )
