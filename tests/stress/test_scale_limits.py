"""Scale arithmetic at large targets. Does not allocate rows or start a database."""

import pytest

from dbscale.core.scale import ScaleTarget, resolve_scale
from helpers import ecommerce_schema

pytestmark = pytest.mark.stress


def test_large_factor_is_exact_integer_math_and_stays_ordered():
    schema = ecommerce_schema()
    plan = resolve_scale([ScaleTarget.parse(f"{n}x") for n in (1, 10, 100, 1_000, 10_000)], schema)
    assert plan.targets[-1].rows["orders"] == schema.table("orders").estimated_rows * 10_000
    totals = [t.total_rows for t in plan.targets]
    assert totals == sorted(totals)
    assert all(n >= 1 for t in plan.targets for n in t.rows.values())
