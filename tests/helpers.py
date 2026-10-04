"""Shared test helpers (no pytest dependency)."""

from __future__ import annotations

import json
from pathlib import Path

from dbscale.core.measurements import LatencyStats, PlanNode, PlanSummary, QueryMeasurement
from dbscale.core.schema import Column, ColumnStats, DataType, ForeignKey, Index, Schema, Table

FIXTURES = Path(__file__).parent / "fixtures"


def col(name: str, dt: DataType = DataType.INTEGER, native: str | None = None, **kw) -> Column:
    natives = {
        DataType.INTEGER: "integer",
        DataType.BIGINT: "bigint",
        DataType.SMALLINT: "smallint",
        DataType.TEXT: "text",
        DataType.VARCHAR: "character varying(255)",
        DataType.UUID: "uuid",
        DataType.TIMESTAMPTZ: "timestamp with time zone",
        DataType.DECIMAL: "numeric(10,2)",
        DataType.BOOLEAN: "boolean",
        DataType.JSON: "jsonb",
        DataType.ENUM: "order_status",
    }
    return Column(name=name, data_type=dt, native_type=native or natives.get(dt, "text"), **kw)


def ecommerce_schema() -> Schema:
    """Hand-built schema mirroring tests/fixtures/schemas/ecommerce.sql (no database needed)."""
    users = Table(
        name="users",
        schema_name="public",
        columns=[
            col("id", nullable=False),
            col("email", DataType.VARCHAR, nullable=False, max_length=255),
            col(
                "full_name",
                DataType.TEXT,
                nullable=False,
                stats=ColumnStats(avg_width=12, distinct_count=2000),
            ),
            col(
                "country",
                DataType.VARCHAR,
                native="character varying(2)",
                nullable=False,
                max_length=2,
                stats=ColumnStats(distinct_count=5, avg_width=3, top_frequencies=[0.4, 0.3, 0.2, 0.1]),
            ),
            col("is_active", DataType.BOOLEAN, nullable=False),
            col("created_at", DataType.TIMESTAMPTZ, nullable=False),
        ],
        primary_key=["id"],
        indexes=[
            Index(name="users_pkey", columns=["id"], unique=True, primary=True),
            Index(
                name="users_email_key",
                columns=["email"],
                unique=True,
                definition="CREATE UNIQUE INDEX users_email_key ON public.users USING btree (email)",
            ),
        ],
        unique_constraints=[["email"]],
        estimated_rows=2000,
    )
    orders = Table(
        name="orders",
        schema_name="public",
        columns=[
            col("id", DataType.BIGINT, nullable=False),
            col("user_id", nullable=False, stats=ColumnStats(distinct_count=2000, top_frequencies=[0.01])),
            col(
                "status",
                DataType.ENUM,
                nullable=False,
                enum_values=["pending", "paid", "shipped", "cancelled"],
            ),
            col("total", DataType.DECIMAL, nullable=False, numeric_precision=12, numeric_scale=2),
            col("note", DataType.TEXT, stats=ColumnStats(null_fraction=0.86, distinct_count=1, avg_width=5)),
            col("created_at", DataType.TIMESTAMPTZ, nullable=False, stats=ColumnStats(correlation=-0.99)),
        ],
        primary_key=["id"],
        foreign_keys=[
            ForeignKey(
                name="orders_user_id_fkey",
                columns=["user_id"],
                referenced_table="users",
                referenced_columns=["id"],
            )
        ],
        indexes=[Index(name="orders_pkey", columns=["id"], unique=True, primary=True)],
        estimated_rows=10000,
    )
    products = Table(
        name="products",
        schema_name="public",
        columns=[
            col("id", nullable=False),
            col("sku", DataType.VARCHAR, native="character varying(32)", nullable=False, max_length=32),
            col("price", DataType.DECIMAL, nullable=False, numeric_precision=10, numeric_scale=2),
        ],
        primary_key=["id"],
        indexes=[
            Index(name="products_pkey", columns=["id"], unique=True, primary=True),
            Index(
                name="products_sku_key",
                columns=["sku"],
                unique=True,
                definition="CREATE UNIQUE INDEX products_sku_key ON public.products USING btree (sku)",
            ),
        ],
        unique_constraints=[["sku"]],
        estimated_rows=500,
    )
    order_items = Table(
        name="order_items",
        schema_name="public",
        columns=[
            col("order_id", DataType.BIGINT, nullable=False),
            col("product_id", nullable=False),
            col("quantity", DataType.SMALLINT, nullable=False),
        ],
        primary_key=["order_id", "product_id"],
        foreign_keys=[
            ForeignKey(
                name="oi_order_fkey",
                columns=["order_id"],
                referenced_table="orders",
                referenced_columns=["id"],
            ),
            ForeignKey(
                name="oi_product_fkey",
                columns=["product_id"],
                referenced_table="products",
                referenced_columns=["id"],
            ),
        ],
        indexes=[
            Index(name="order_items_pkey", columns=["order_id", "product_id"], unique=True, primary=True),
            Index(
                name="idx_order_items_product",
                columns=["product_id"],
                definition="CREATE INDEX idx_order_items_product ON public.order_items USING btree (product_id)",
            ),
        ],
        estimated_rows=25000,
    )
    categories = Table(
        name="categories",
        schema_name="public",
        columns=[col("id", nullable=False), col("name", DataType.TEXT, nullable=False), col("parent_id")],
        primary_key=["id"],
        foreign_keys=[
            ForeignKey(
                name="cat_parent_fkey",
                columns=["parent_id"],
                referenced_table="categories",
                referenced_columns=["id"],
            )
        ],
        estimated_rows=20,
    )
    return Schema(
        database_type="postgres",
        database_version="16.1",
        tables=[order_items, orders, users, products, categories],
    )


def load_plan(name: str) -> PlanSummary:
    from dbscale.adapters.postgres.plan import parse_explain_json

    raw = json.loads((FIXTURES / "plans" / f"{name}.json").read_text())
    root, planning, execution = parse_explain_json(raw)
    return PlanSummary.from_root(root, planning, execution)


def measurement(
    query: str,
    label: str,
    factor: float,
    p50: float,
    *,
    plan: PlanSummary | PlanNode | None = None,
    rows_returned: int = 10,
    error: str | None = None,
    total_rows: int | None = None,
) -> QueryMeasurement:
    if isinstance(plan, PlanNode):
        plan = PlanSummary.from_root(plan)
    samples = [p50 * 0.9, p50, p50 * 1.1, p50, p50 * 1.3]
    return QueryMeasurement(
        query_name=query,
        scale_label=label,
        scale_factor=factor,
        total_rows=total_rows or int(1000 * factor),
        latency=None if error else LatencyStats.from_samples(samples),
        samples_ms=[] if error else samples,
        rows_returned=None if error else rows_returned,
        plan=None if error else plan,
        error=error,
    )
