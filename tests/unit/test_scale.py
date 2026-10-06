import pytest

from dbscale.core.scale import ScaleError, ScaleTarget, format_count, parse_count, resolve_scale


@pytest.mark.parametrize(
    "raw,expected",
    [
        (1000, 1000),
        ("1000", 1000),
        ("1_000_000", 1_000_000),
        ("1M", 1_000_000),
        ("10m", 10_000_000),
        ("250k", 250_000),
        ("2.5K", 2500),
        ("1B", 1_000_000_000),
    ],
)
def test_parse_count(raw, expected):
    assert parse_count(raw) == expected


@pytest.mark.parametrize("raw", ["ten", "1x", "", None, True, "1.5.5"])
def test_parse_count_rejects_garbage(raw):
    with pytest.raises(ScaleError):
        parse_count(raw)


def test_parse_factor_targets():
    assert ScaleTarget.parse("10x").factor == 10
    assert ScaleTarget.parse("2.5X").factor == 2.5
    assert ScaleTarget.parse(100).factor == 100
    assert ScaleTarget.parse({"factor": 3}).factor == 3
    assert ScaleTarget.parse("10x").label == "10x"
    assert ScaleTarget.parse("2.5x").label == "2.5x"


def test_parse_row_targets():
    t = ScaleTarget.parse("1M users")
    assert t.rows == {"users": 1_000_000} and t.factor is None
    t = ScaleTarget.parse({"users": "10M", "orders": 100_000_000})
    assert t.rows == {"users": 10_000_000, "orders": 100_000_000}
    assert t.label == "users=10M,orders=100M"
    assert ScaleTarget.parse({"rows": {"users": "1k"}}).rows == {"users": 1000}


def test_parse_rejects_invalid():
    with pytest.raises(ScaleError):
        ScaleTarget.parse("lots")
    with pytest.raises(ScaleError):
        ScaleTarget.parse(True)


def test_resolve_uniform_factor(schema):
    plan = resolve_scale([ScaleTarget.parse("10x"), ScaleTarget.parse("1x")], schema)
    assert [t.label for t in plan.targets] == ["1x", "10x"]  # sorted by size
    ten = plan.target("10x")
    assert ten.rows["users"] == 20_000
    assert ten.rows["orders"] == 100_000
    assert ten.factor == pytest.approx(10.0)
    assert plan.baseline_rows["orders"] == 10_000


def test_resolve_explicit_rows_leaves_unlisted_tables_at_baseline(schema):
    plan = resolve_scale([ScaleTarget.parse({"users": "200k"})], schema)
    t = plan.targets[0]
    assert t.rows["users"] == 200_000
    assert t.rows["orders"] == plan.baseline_rows["orders"]
    assert t.rows["products"] == plan.baseline_rows["products"]
    assert t.label == "users=200K"


def test_resolve_mixed_explicit_rows_leaves_unlisted_at_baseline(schema):
    plan = resolve_scale([ScaleTarget.parse({"users": "20k", "orders": "10M"})], schema)
    t = plan.targets[0]
    assert t.rows["users"] == 20_000 and t.rows["orders"] == 10_000_000
    assert t.rows["products"] == plan.baseline_rows["products"]


def test_resolve_empty_tables_use_base_rows(schema):
    for t in schema.tables:
        t.estimated_rows = 0
    plan = resolve_scale([ScaleTarget.parse("10x")], schema, base_rows=500)
    assert plan.targets[0].rows["users"] == 5000


def test_resolve_unknown_table(schema):
    with pytest.raises(ScaleError, match="unknown table"):
        resolve_scale([ScaleTarget.parse({"nope": 10})], schema)


def test_resolve_duplicate_labels(schema):
    with pytest.raises(ScaleError, match="Duplicate"):
        resolve_scale([ScaleTarget.parse("10x"), ScaleTarget.parse(10)], schema)


def test_format_count():
    assert format_count(999) == "999"
    assert format_count(1500) == "1.5K"
    assert format_count(2_000_000) == "2M"
    assert format_count(3_500_000_000) == "3.5B"
