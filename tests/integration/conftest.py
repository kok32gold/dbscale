"""Integration fixtures: one disposable PostgreSQL container for the whole session.

The container hosts two databases:
* ``source``  — the fixture e-commerce schema, playing the role of the user's database
* ``dbscale`` — the sandbox DBScale populates (used via ``sandbox.type: url``)
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from dbscale.infrastructure import DockerPostgresSandbox

FIXTURE_SQL = Path(__file__).parent.parent / "fixtures" / "schemas" / "ecommerce.sql"


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=20).returncode == 0
    except Exception:  # noqa: BLE001
        return False


def pytest_collection_modifyitems(config, items):
    if _docker_available():
        return
    skip = pytest.mark.skip(reason="Docker is not available")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def pg_container():
    image = os.environ.get("DBSCALE_TEST_PG_IMAGE", "postgres:16-alpine")
    sandbox = DockerPostgresSandbox(image=image, name="dbscale-test-" + __import__("secrets").token_hex(3))
    sandbox_url = sandbox.start()
    try:
        import psycopg

        with psycopg.connect(sandbox_url, autocommit=True) as conn:
            conn.execute("CREATE DATABASE source")
        source_url = sandbox_url.rsplit("/", 1)[0] + "/source"
        with psycopg.connect(source_url, autocommit=True) as conn:
            conn.execute(FIXTURE_SQL.read_text())
        yield {"source": source_url, "sandbox": sandbox_url}
    finally:
        sandbox.destroy()


@pytest.fixture
def source_url(pg_container) -> str:
    return pg_container["source"]


@pytest.fixture
def sandbox_url(pg_container) -> str:
    return pg_container["sandbox"]
