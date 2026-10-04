from dbscale.core.schema import ColumnStats, DataType, Schema, Table
from dbscale.generation import Picker, ValueKind, plan_generation
from helpers import col


def test_tables_are_in_dependency_order(schema):
    plan = plan_generation(schema, seed=7)
    names = [t.table for t in plan.tables]
    assert names.index("users") < names.index("orders") < names.index("order_items")
    assert plan.seed == 7


def test_primary_keys_are_sequences(schema):
    plan = plan_generation(schema)
    spec = plan.table("users").column("id").spec
    assert spec.kind == ValueKind.KEY_REF and spec.picker == Picker.SEQUENCE
    assert spec.ref_table == "users" and spec.ref_column == "id"
    assert plan.table("users").is_unique_safe(["id"])


def test_unique_text_column_is_sequence_derived(schema):
    plan = plan_generation(schema)
    email = plan.table("users").column("email")
    assert email.spec.kind == ValueKind.KEY_REF and email.spec.picker == Picker.SEQUENCE
    assert email.null_fraction == 0.0
    assert plan.table("users").is_unique_safe(["email"])
    assert plan.table("products").is_unique_safe(["sku"])


def test_foreign_key_references_parent(schema):
    plan = plan_generation(schema)
    spec = plan.table("orders").column("user_id").spec
    assert spec.kind == ValueKind.KEY_REF
    assert spec.ref_table == "users" and spec.ref_column == "id"
    assert spec.picker == Picker.UNIFORM  # top frequency 1% -> no skew


def test_skewed_foreign_key_when_hot_parents():
    users = Table(name="users", columns=[col("id", nullable=False)], primary_key=["id"], estimated_rows=10)
    events = Table(
        name="events",
        columns=[col("id", nullable=False), col("user_id", stats=ColumnStats(top_frequencies=[0.3, 0.2]))],
        primary_key=["id"],
        foreign_keys=[
            __import__("dbscale.core.schema", fromlist=["ForeignKey"]).ForeignKey(
                name="fk", columns=["user_id"], referenced_table="users", referenced_columns=["id"]
            )
        ],
        estimated_rows=100,
    )
    plan = plan_generation(Schema(database_type="postgres", tables=[users, events]))
    spec = plan.table("events").column("user_id").spec
    assert spec.picker == Picker.SKEWED and spec.skew > 1


def test_composite_primary_key_uses_mixed_radix(schema):
    plan = plan_generation(schema)
    items = plan.table("order_items")
    order_id, product_id = items.column("order_id").spec, items.column("product_id").spec
    assert order_id.picker == Picker.RADIX and order_id.radix_position == 0
    assert product_id.picker == Picker.RADIX and product_id.radix_position == 1
    assert order_id.radix == ["orders", "products"]
    assert order_id.ref_table == "orders" and product_id.ref_table == "products"
    assert items.is_unique_safe(["order_id", "product_id"])
    assert not items.is_unique_safe(["product_id"])


def test_self_reference(schema):
    plan = plan_generation(schema)
    spec = plan.table("categories").column("parent_id").spec
    assert spec.picker == Picker.SELF_REF and spec.ref_table == "categories"


def test_null_fractions_follow_stats(schema):
    plan = plan_generation(schema)
    orders = plan.table("orders")
    assert orders.column("note").null_fraction == 0.86
    assert orders.column("user_id").null_fraction == 0.0  # not nullable
    assert (
        plan.table("categories").column("parent_id").null_fraction == 0.0
    )  # FKs without stats stay populated
    assert plan.table("categories").column("name").null_fraction == 0.0  # NOT NULL


def test_scalar_specs(schema):
    plan = plan_generation(schema)
    orders = plan.table("orders")
    assert orders.column("status").spec.kind == ValueKind.ENUM
    assert orders.column("status").spec.values == ["pending", "paid", "shipped", "cancelled"]
    total = orders.column("total").spec
    assert total.kind == ValueKind.DECIMAL and total.decimals == 2
    created = orders.column("created_at").spec
    assert created.kind == ValueKind.TIMESTAMP and created.ordered  # strong correlation
    users = plan.table("users")
    assert users.column("is_active").spec.kind == ValueKind.BOOLEAN
    country = users.column("country").spec
    assert country.kind == ValueKind.TEXT and country.distinct == 5 and country.length == 2
    full_name = users.column("full_name").spec
    assert full_name.kind == ValueKind.TEXT and full_name.distinct is None and full_name.length == 12


def test_common_values_become_weighted_choice(schema):
    schema.table("users").column("country").stats.common_values = ["US", "DE", "GB", "FR"]
    schema.table("orders").column("status").stats = ColumnStats(
        top_frequencies=[0.7, 0.3], common_values=["paid", "pending"]
    )
    plan = plan_generation(schema)
    country = plan.table("users").column("country").spec
    assert country.kind == ValueKind.CHOICE
    assert country.values == ["US", "DE", "GB", "FR"] and country.weights == [0.4, 0.3, 0.2, 0.1]
    assert country.fallback == ValueKind.TEXT
    status = plan.table("orders").column("status").spec
    assert status.kind == ValueKind.CHOICE and status.fallback == ValueKind.ENUM


def test_generated_columns_are_skipped_and_unknown_types_noted():
    t = Table(
        name="t",
        columns=[
            col("id", nullable=False),
            col("doubled", is_generated=True, default="(id * 2)"),
            col("geo", DataType.OTHER, native="geometry"),
        ],
        primary_key=["id"],
        estimated_rows=5,
    )
    plan = plan_generation(Schema(database_type="postgres", tables=[t]))
    names = [c.name for c in plan.table("t").columns]
    assert "doubled" not in names
    assert plan.table("t").column("geo").spec.kind == ValueKind.NULL
    assert any("geo" in n for n in plan.notes)


def test_builtin_arrays_are_not_reported_as_unsupported():
    t = Table(
        name="t",
        columns=[
            col("id", nullable=False),
            col(
                "tags",
                DataType.ARRAY,
                native="uuid[]",
                array_element=DataType.UUID,
                nullable=False,
            ),
        ],
        primary_key=["id"],
        estimated_rows=5,
    )
    plan = plan_generation(Schema(database_type="postgres", tables=[t]))
    spec = plan.table("t").column("tags").spec
    assert spec.kind == ValueKind.ARRAY
    assert spec.element is not None and spec.element.kind == ValueKind.UUID
    assert not any("unsupported" in note for note in plan.notes)


def test_composite_unique_constraint_is_noted():
    t = Table(
        name="t",
        columns=[col("id", nullable=False), col("a"), col("b")],
        primary_key=["id"],
        unique_constraints=[["a", "b"]],
        estimated_rows=5,
    )
    plan = plan_generation(Schema(database_type="postgres", tables=[t]))
    assert any("composite unique" in n for n in plan.notes)
    assert not plan.table("t").is_unique_safe(["a", "b"])
