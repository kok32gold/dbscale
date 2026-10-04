"""Helpers for integration tests."""

from pathlib import Path

QUERIES = Path(__file__).parent.parent / "fixtures" / "queries"


def read_query(name: str) -> str:
    return (QUERIES / f"{name}.sql").read_text()
