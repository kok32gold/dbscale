"""PostgreSQL adapter against a real server: inspection, reproduction, population, execution, plans."""

from __future__ import annotations

import os
import re

import pytest

from dbscale.adapters import AdapterError, get_adapter
from dbscale.adapters.postgres import PostgresAdapter
from dbscale.core.measurements import NodeKind, PlanSummary
from dbscale.core.scale import ScaleTarget, resolve_scale
from dbscale.core.schema import Column, DataType, ForeignKey, Index, Schema, Table
from dbscale.generation import plan_generation

pytestmark = pytest.mark.integration


@pytest.fixture
def source(source_url):
    with PostgresAdapter.connect(source_url, read_only=True) as adapter:
        yield adapter


@pytest.fixture
def sandbox(sandbox_url):
    with PostgresAdapter.connect(sandbox_url) as adapter:
        yield adapter


def test_registry_connects(source_url):
    adapter = get_adapter("postgres").connect(source_url, read_only=True)
    info = adapter.server_info()
    assert info.type == "postgres"
    assert info.version.split(".", 1)[0] == _postgres_major()
    adapter.close()


def _postgres_major() -> str:
    """Major version of ``DBSCALE_TEST_PG_IMAGE``. CI runs 15 and 16; local default is 16."""
    image = os.environ.get("DBSCALE_TEST_PG_IMAGE", "postgres:16-alpine")
    match = re.search(r":(\d+)(?:\.|-|$)", image)
    return match.group(1) if match else "16"


def test_read_only_connection_refuses_writes(source, source_url):
    schema = source.inspect_schema()
    with pytest.raises(AdapterError, match="read-only"):
        source.create_schema(schema)
    # even raw SQL cannot write through the read-only session
    with pytest.raises(AdapterError):
        source.execute("CREATE TABLE should_not_exist (id int)", timeout_ms=5000)


def test_inspect_schema(source):
    schema = source.inspect_schema(["public"])
    assert set(schema.table_names()) == {"users", "categories", "products", "orders", "order_items", "events"}

    orders = schema.table("orders")
    assert orders.primary_key == ["id"]
    assert orders.estimated_rows == pytest.approx(10_000, rel=0.05)
    assert [fk.referenced_table for fk in orders.foreign_keys] == ["users"]
    status = orders.column("status")
    assert status.data_type == DataType.ENUM and status.enum_values == [
        "pending",
        "paid",
        "shipped",
        "cancelled",
    ]
    assert orders.column("total").numeric_precision == 12 and orders.column("total").numeric_scale == 2
    assert orders.column("note").nullable and orders.column("note").stats.null_fraction == pytest.approx(
        0.86, abs=0.03
    )
    assert not orders.column("user_id").nullable
    assert orders.column("user_id").stats.distinct_count == pytest.approx(2000, rel=0.1)
    # privacy: no values are read by default
    assert all(c.stats is None or c.stats.common_values is None for t in schema.tables for c in t.columns)

    users = schema.table("users")
    assert users.column("email").max_length == 255
    assert users.is_unique_column("email")
    email_ix = next(ix for ix in users.indexes if ix.name == "users_email_key")
    assert email_ix.unique and email_ix.columns == ["email"] and "CREATE UNIQUE INDEX" in email_ix.definition

    items = schema.table("order_items")
    assert items.primary_key == ["order_id", "product_id"]
    assert len(items.foreign_keys) == 2

    categories = schema.table("categories")
    assert categories.foreign_keys[0].referenced_table == "categories"  # self reference

    order = [t.name for t in schema.topological_order()]
    assert order.index("users") < order.index("orders") < order.index("order_items")


def test_inspect_with_sampled_common_values(source):
    schema = source.inspect_schema(["public"], sample_common_values=True)
    country = schema.table("users").column("country").stats
    assert set(country.common_values) == {"US", "DE", "GB", "FR", "BR"}
    assert len(country.top_frequencies) == len(country.common_values)
    # high-cardinality columns never get values, even when opted in
    assert schema.table("users").column("email").stats.common_values is None


def test_include_exclude_filters(source):
    only = source.inspect_schema(["public"], include_tables=["users", "ord*"])
    assert set(only.table_names()) == {"users", "orders", "order_items"}
    without = source.inspect_schema(["public"], exclude_tables=["events"])
    assert "events" not in without.table_names()


def test_reproduce_populate_and_query(source, sandbox):
    schema = source.inspect_schema(["public"], sample_common_values=True)
    plan = plan_generation(schema, seed=1)
    scale = resolve_scale([ScaleTarget.parse("2x")], schema).targets[0]

    sandbox.drop_schema(schema)
    sandbox.create_schema(schema)
    progress: list[tuple[str, int, int]] = []
    sandbox.populate(schema, plan, scale, progress=lambda t, d, n: progress.append((t, d, n)))
    sandbox.create_indexes(schema, plan)
    sandbox.create_foreign_keys(schema)
    sandbox.analyze(schema)

    counts = sandbox.row_counts(schema)
    assert counts == scale.rows
    assert progress and progress[-1][1] == progress[-1][2]

    # referential integrity without ever validating the constraint
    orphans = sandbox.execute(
        "SELECT 1 FROM orders o LEFT JOIN users u ON u.id = o.user_id WHERE u.id IS NULL", timeout_ms=10_000
    )
    assert orphans.rows_returned == 0
    orphans = sandbox.execute(
        "SELECT 1 FROM order_items oi LEFT JOIN orders o ON o.id = oi.order_id WHERE o.id IS NULL",
        timeout_ms=10_000,
    )
    assert orphans.rows_returned == 0
    # self reference points at smaller ids
    bad_parents = sandbox.execute("SELECT 1 FROM categories WHERE parent_id >= id", timeout_ms=10_000)
    assert bad_parents.rows_returned == 0
    # sampled common values and enum labels are reproduced
    assert sandbox.execute("SELECT 1 FROM users WHERE country = 'US'", timeout_ms=10_000).rows_returned > 0
    assert sandbox.execute("SELECT 1 FROM orders WHERE status = 'paid'", timeout_ms=10_000).rows_returned > 0
    # unique text keys follow the documented derivation
    assert sandbox.execute("SELECT 1 FROM products WHERE sku = 'sku-7'", timeout_ms=10_000).rows_returned == 1
    assert (
        sandbox.execute("SELECT 1 FROM users WHERE email = 'email-1'", timeout_ms=10_000).rows_returned == 1
    )
    # ordered timestamps increase with id
    assert (
        sandbox.execute(
            "SELECT 1 FROM (SELECT created_at, lag(created_at) OVER (ORDER BY id) prev FROM orders) s WHERE created_at < prev - interval '1 hour'",
            timeout_ms=10_000,
        ).rows_returned
        == 0
    )

    # execution + explain
    result = sandbox.execute("SELECT * FROM orders WHERE user_id = 42", timeout_ms=10_000)
    assert result.elapsed_ms > 0
    explained = sandbox.explain("SELECT * FROM orders WHERE user_id = 42", timeout_ms=10_000)
    summary = PlanSummary.from_root(explained.root, explained.planning_ms, explained.execution_ms)
    assert summary.seq_scans and summary.seq_scans[0].relation == "orders"
    assert summary.rows_scanned == scale.rows["orders"]
    explained = sandbox.explain("SELECT * FROM users WHERE id = 42", timeout_ms=10_000)
    summary = PlanSummary.from_root(explained.root)
    assert summary.scans[0].kind in (NodeKind.INDEX_SCAN, NodeKind.INDEX_ONLY_SCAN)

    # repopulating at a new scale after dropping FKs/indexes keeps counts exact.
    # Foreign keys first: one may depend on a non-primary unique index.
    bigger = resolve_scale([ScaleTarget.parse("3x")], schema).targets[0]
    sandbox.drop_foreign_keys(schema)
    sandbox.drop_indexes(schema)
    sandbox.truncate(schema)
    sandbox.populate(schema, plan, bigger)
    sandbox.create_indexes(schema, plan)
    assert sandbox.row_counts(schema) == bigger.rows
    sandbox.drop_schema(schema)


def _unique_index_referenced_by_foreign_key() -> Schema:
    """TypeORM-style unique index that a foreign key targets, not the primary key."""
    return Schema(
        database_type="postgres",
        tables=[
            Table(
                name="accounts",
                schema_name="public",
                columns=[
                    Column(name="id", data_type=DataType.INTEGER, native_type="integer", nullable=False),
                    Column(
                        name="code", data_type=DataType.VARCHAR, native_type="varchar(32)", nullable=False
                    ),
                ],
                primary_key=["id"],
                indexes=[
                    Index(name="accounts_pkey", columns=["id"], unique=True, primary=True),
                    Index(
                        name="UQ_accounts_code",
                        columns=["code"],
                        unique=True,
                        definition='CREATE UNIQUE INDEX "UQ_accounts_code" ON public.accounts USING btree (code)',
                    ),
                ],
            ),
            Table(
                name="audit_logs",
                schema_name="public",
                columns=[
                    Column(name="id", data_type=DataType.INTEGER, native_type="integer", nullable=False),
                    Column(
                        name="account_code",
                        data_type=DataType.VARCHAR,
                        native_type="varchar(32)",
                        nullable=False,
                    ),
                ],
                primary_key=["id"],
                indexes=[Index(name="audit_logs_pkey", columns=["id"], unique=True, primary=True)],
                foreign_keys=[
                    ForeignKey(
                        name="FK_audit_logs_account",
                        columns=["account_code"],
                        referenced_table="accounts",
                        referenced_columns=["code"],
                    )
                ],
            ),
        ],
    )


def test_reload_drops_foreign_keys_before_unique_indexes_they_depend_on(sandbox):
    schema = _unique_index_referenced_by_foreign_key()
    plan = plan_generation(schema, seed=1)
    sandbox.drop_schema(schema)
    try:
        sandbox.create_schema(schema)
        sandbox.create_indexes(schema, plan)
        sandbox.create_foreign_keys(schema)
        with pytest.raises(AdapterError, match="depends on"):
            sandbox.drop_indexes(schema)
        sandbox.drop_foreign_keys(schema)
        sandbox.drop_indexes(schema)
        sandbox.create_indexes(schema, plan)
        sandbox.create_foreign_keys(schema)
        still_there = sandbox.execute(
            """
            SELECT 1
            FROM pg_constraint
            WHERE conname = 'FK_audit_logs_account' AND contype = 'f'
            """,
            timeout_ms=5_000,
        )
        assert still_there.rows_returned == 1
    finally:
        sandbox.drop_schema(schema)


def test_parallel_load_covers_every_key_and_restores_the_primary_key(sandbox, monkeypatch):
    """Two backends insert disjoint slices. The primary key is rebuilt after the heap load."""
    monkeypatch.setattr(
        "dbscale.adapters.postgres.adapter.load_workers",
        lambda total, **_kwargs: 2 if total >= 100 else 1,
    )
    schema = Schema(
        database_type="postgres",
        tables=[
            Table(
                name="bulk",
                schema_name="public",
                columns=[
                    Column(name="id", data_type=DataType.INTEGER, native_type="integer", nullable=False)
                ],
                primary_key=["id"],
                indexes=[Index(name="bulk_pkey", columns=["id"], unique=True, primary=True)],
                estimated_rows=200,
            )
        ],
    )
    plan = plan_generation(schema, seed=7)
    scale = resolve_scale([ScaleTarget.parse("1x")], schema).targets[0]
    sandbox.drop_schema(schema)
    try:
        sandbox.create_schema(schema)
        sandbox.populate(schema, plan, scale)
        sandbox.create_indexes(schema, plan)
        counts = sandbox.execute(
            "SELECT count(*), count(DISTINCT id), min(id), max(id) FROM bulk", timeout_ms=10_000
        )
        # execute() only returns a row count, so check integrity with predicates.
        assert sandbox.row_counts(schema) == {"bulk": 200}
        assert (
            sandbox.execute(
                "SELECT 1 FROM bulk GROUP BY id HAVING count(*) > 1", timeout_ms=10_000
            ).rows_returned
            == 0
        )
        assert sandbox.execute("SELECT 1 FROM bulk WHERE id = 1", timeout_ms=10_000).rows_returned == 1
        assert sandbox.execute("SELECT 1 FROM bulk WHERE id = 200", timeout_ms=10_000).rows_returned == 1
        assert counts.rows_returned == 1
        pk = sandbox.execute(
            "SELECT 1 FROM pg_constraint WHERE conrelid = 'bulk'::regclass AND contype = 'p'",
            timeout_ms=5_000,
        )
        assert pk.rows_returned == 1
        # 200 integer rows fit in one page, so the cost model seq-scans even with a valid
        # primary key. Turning sequential scans off proves the rebuilt index can serve the lookup.
        sandbox.execute("SET enable_seqscan = off", timeout_ms=5_000)
        try:
            explained = sandbox.explain("SELECT * FROM bulk WHERE id = 42", timeout_ms=10_000)
            summary = PlanSummary.from_root(explained.root)
            assert summary.scans[0].kind in (NodeKind.INDEX_SCAN, NodeKind.INDEX_ONLY_SCAN)
        finally:
            sandbox.execute("RESET enable_seqscan", timeout_ms=5_000)
    finally:
        sandbox.drop_schema(schema)


def test_timeout_is_reported(sandbox):
    with pytest.raises(AdapterError, match="timeout"):
        sandbox.execute("SELECT pg_sleep(2)", timeout_ms=200)
    # the connection is still usable afterwards (autocommit, no aborted transaction)
    assert sandbox.execute("SELECT 1", timeout_ms=1000).rows_returned == 1


def test_sql_errors_are_adapter_errors(sandbox):
    with pytest.raises(AdapterError, match="does not exist"):
        sandbox.execute("SELECT * FROM no_such_table", timeout_ms=1000)
