"""Shared pytest fixtures. Helpers live in tests/helpers.py."""

from __future__ import annotations

import pytest

from dbscale.core.schema import Schema
from helpers import ecommerce_schema


@pytest.fixture
def schema() -> Schema:
    return ecommerce_schema()
