"""Secrets stay out of errors, reports, and generated SQL identifiers stay quoted."""

import httpx
import psycopg
import pytest

from dbscale.adapters.postgres.adapter import PostgresAdapter
from dbscale.adapters.postgres.ddl import create_table_statement, qualified
from dbscale.advisors.llm.providers import OpenAICompatibleProvider
from dbscale.core.schema import Table
from dbscale.redact import redact_secrets
from helpers import col


def test_redact_urls_tokens_and_leaves_hosts():
    text = "postgresql://app:s3cret@db.internal:5432/shop Bearer sk-live-abcdef123456 failed"
    cleaned = redact_secrets(text)
    assert "s3cret" not in cleaned
    assert "sk-live-abcdef123456" not in cleaned
    assert "db.internal:5432" in cleaned
    assert "app:***@" in cleaned
    assert "Bearer ***" in cleaned


def test_connect_error_does_not_repeat_the_password(monkeypatch):
    def boom(url, **_kwargs):
        raise psycopg.OperationalError(f"connection to {url} failed: password authentication failed")

    monkeypatch.setattr(psycopg, "connect", boom)
    with pytest.raises(Exception, match="authentication failed") as exc:
        PostgresAdapter.connect("postgresql://app:s3cret@db.internal/shop")
    assert "s3cret" not in str(exc.value)
    assert "db.internal" in str(exc.value)


def test_llm_error_redacts_bearer_token(monkeypatch):
    def boom(*_args, **_kwargs):
        raise httpx.ConnectError("timed out sending Authorization: Bearer sk-live-abcdef123456")

    monkeypatch.setattr("dbscale.advisors.llm.providers.httpx.post", boom)
    provider = OpenAICompatibleProvider("gpt-4o-mini", api_key="sk-live-abcdef123456")
    with pytest.raises(Exception, match="timed out") as exc:
        provider.complete("system", "user")
    assert "sk-live-abcdef123456" not in str(exc.value)


def test_unusual_identifiers_are_quoted_in_ddl():
    table = Table(
        name='odd"name',
        schema_name="public",
        columns=[col("select", nullable=False), col("user id", nullable=False)],
        primary_key=["select"],
    )
    sql = create_table_statement(table, unlogged=False)
    assert qualified(table) == '"public"."odd""name"'
    assert '"select"' in sql
    assert '"user id"' in sql
    assert "DROP TABLE" not in sql


def test_query_file_with_shell_metacharacters_is_a_path(tmp_path):
    from dbscale.core.workload import QuerySpec

    name = "q; rm -rf /"
    path = tmp_path / name
    path.write_text("SELECT 1")
    spec = QuerySpec(name="q", file=name).resolve(tmp_path)
    assert spec.sql == "SELECT 1"


def test_missing_env_error_names_the_variable_not_other_secrets(monkeypatch):
    from dbscale.core.config import ConfigError, expand_env

    monkeypatch.setenv("OTHER_SECRET", "do-not-leak")
    with pytest.raises(ConfigError, match="DATABASE_URL") as exc:
        expand_env("${DATABASE_URL}", {"OTHER_SECRET": "do-not-leak"})
    assert "do-not-leak" not in str(exc.value)
