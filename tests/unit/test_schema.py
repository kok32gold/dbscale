from dbscale.core.schema import ColumnStats, DataType


def test_lookup_by_bare_or_qualified_name(schema):
    assert schema.table("orders") is not None
    assert schema.table("public.orders") is schema.table("orders")
    assert schema.table("missing") is None
    assert schema.table("orders").column("user_id").data_type == DataType.INTEGER
    assert schema.table("orders").column("nope") is None


def test_topological_order_puts_parents_first(schema):
    order = [t.name for t in schema.topological_order()]
    assert order.index("users") < order.index("orders") < order.index("order_items")
    assert order.index("products") < order.index("order_items")
    assert len(order) == len(schema.tables)
    # self-referencing table does not break ordering
    assert "categories" in order


def test_total_rows_and_names(schema):
    assert schema.total_rows == 2000 + 10000 + 500 + 25000 + 20
    assert set(schema.table_names()) == {"users", "orders", "products", "order_items", "categories"}


def test_unique_detection(schema):
    users = schema.table("users")
    assert users.is_unique_column("id")
    assert users.is_unique_column("email")
    assert not users.is_unique_column("country")
    items = schema.table("order_items")
    assert not items.is_unique_column("order_id")  # composite PK component only


def test_foreign_key_lookup(schema):
    fk = schema.table("orders").foreign_key_for("user_id")
    assert fk is not None and fk.referenced_table == "users" and not fk.is_composite
    assert schema.table("orders").foreign_key_for("total") is None


def test_datatype_categories():
    assert DataType.BIGINT.is_integer and DataType.BIGINT.is_numeric
    assert DataType.DECIMAL.is_numeric and not DataType.DECIMAL.is_integer
    assert DataType.VARCHAR.is_textual
    assert DataType.DATE.is_temporal
    assert not DataType.JSON.is_textual


def test_column_stats_skew():
    assert ColumnStats().skew == 0.0
    assert ColumnStats(top_frequencies=[0.5, 0.3]).skew == 0.8
    assert ColumnStats(top_frequencies=[0.9, 0.9]).skew == 1.0
