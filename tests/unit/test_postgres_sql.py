"""Pure-Python tests of the PostgreSQL DDL and synthetic-SQL compilers (no database)."""

from dbscale.adapters.postgres import ddl
from dbscale.adapters.postgres.synth import SqlValueCompiler, key_expr
from dbscale.core.schema import DataType, Schema, Table
from dbscale.generation import plan_generation
from helpers import col


def _compiler(schema, rows=None):
    plan = plan_generation(schema)
    rows = rows or {t.name: 100 for t in schema.tables}
    return plan, SqlValueCompiler(schema, plan, rows)


def test_key_expr_by_type():
    t = Table(
        name="t",
        columns=[
            col("id"),
            col("u", DataType.UUID),
            col("code", DataType.VARCHAR, max_length=8),
            col("slug", DataType.TEXT),
        ],
    )
    assert key_expr(t, t.column("id"), "g.i") == "(g.i)"
    uuid_key = key_expr(t, t.column("u"), "g.i")
    assert uuid_key == "lpad(to_hex((g.i)::bigint), 32, '0')::uuid"
    assert key_expr(t, t.column("code"), "g.i") == "(g.i)::text"  # short varchar: bare number
    assert key_expr(t, t.column("slug"), "g.i") == "'slug-' || (g.i)::text"


def test_foreign_key_uses_same_derivation_as_parent(schema):
    plan, c = _compiler(
        schema, {"users": 2000, "orders": 10000, "products": 500, "order_items": 25000, "categories": 20}
    )
    users, orders = schema.table("users"), schema.table("orders")
    pk = c.column_expr(users, users.column("id"), plan.table("users").column("id"), 2000)
    fk = c.column_expr(orders, orders.column("user_id"), plan.table("orders").column("user_id"), 10000)
    assert pk == "((g.i))::integer"
    assert "hashint8" in fk and "* 2000)" in fk and fk.endswith("::integer")
    assert "random()" not in fk


def test_composite_key_mixed_radix(schema):
    rows = {"users": 10, "orders": 100, "products": 5, "order_items": 500, "categories": 3}
    plan, c = _compiler(schema, rows)
    items = schema.table("order_items")
    tp = plan.table("order_items")
    order_expr = c.column_expr(items, items.column("order_id"), tp.column("order_id"), 500)
    product_expr = c.column_expr(items, items.column("product_id"), tp.column("product_id"), 500)
    assert "((g.i - 1)) / 1) % 100" in order_expr
    assert "((g.i - 1)) / 100) % 5" in product_expr
    assert c.row_cap(tp) == 500  # orders * products


def test_null_wrapping_and_casts(schema):
    plan, c = _compiler(schema)
    orders = schema.table("orders")
    note = c.column_expr(orders, orders.column("note"), plan.table("orders").column("note"), 100)
    assert note.startswith("CASE WHEN ") and "0.860000" in note and "hashint8" in note
    assert "random()" not in note
    assert note.endswith("::text END")
    status = c.column_expr(orders, orders.column("status"), plan.table("orders").column("status"), 100)
    assert "ARRAY['pending', 'paid', 'shipped', 'cancelled']" in status and status.endswith("::order_status")


def test_choice_is_a_hash_of_the_row_number(schema):
    schema.table("users").column("country").stats.common_values = ["US", "DE", "GB", "FR"]
    plan, c = _compiler(schema)
    users = schema.table("users")
    expr = c.column_expr(users, users.column("country"), plan.table("users").column("country"), 100)
    assert "WHEN " in expr and "0.400000" in expr and "THEN 'US'" in expr
    # hashint8(g.i) is per row. A bare random() would be hoisted, or volatile and single-threaded.
    assert "hashint8" in expr and "g.i" in expr and "random()" not in expr and "SELECT " not in expr


def test_ordered_timestamps_use_total_rows(schema):
    plan, c = _compiler(schema)
    orders = schema.table("orders")
    expr = c.column_expr(
        orders, orders.column("created_at"), plan.table("orders").column("created_at"), 12345
    )
    assert "/ 12345" in expr and "now() - interval '730 days'" in expr


def test_select_list_skips_generated_columns():
    t = Table(
        name="t",
        schema_name="public",
        columns=[col("id", nullable=False), col("twice", is_generated=True, default="(id * 2)")],
        primary_key=["id"],
        estimated_rows=1,
    )
    schema = Schema(database_type="postgres", tables=[t])
    plan, c = _compiler(schema)
    names, exprs = c.select_list(t, plan.table("t"), 10)
    assert names == ['"id"'] and len(exprs) == 1


# ------------------------------------------------------------------- DDL


def test_generated_expressions_do_not_call_random_or_md5(schema):
    plan, c = _compiler(schema)
    for table in schema.tables:
        table_plan = plan.table(table.name)
        for cp in table_plan.columns:
            col = table.column(cp.name)
            if col is None or col.is_generated:
                continue
            expr = c.column_expr(table, col, cp, 1000)
            assert "random()" not in expr
            assert "md5(" not in expr


def test_create_table_statement(schema):
    sql = ddl.create_table_statement(schema.table("order_items"))
    assert sql.startswith('CREATE UNLOGGED TABLE "public"."order_items"')
    assert '"order_id" bigint NOT NULL' in sql
    assert 'PRIMARY KEY ("order_id", "product_id")' in sql
    assert "UNLOGGED" not in ddl.create_table_statement(schema.table("users"), unlogged=False)


def test_generated_column_ddl():
    t = Table(
        name="t", columns=[col("id", nullable=False), col("twice", is_generated=True, default="(id * 2)")]
    )
    assert '"twice" integer GENERATED ALWAYS AS ((id * 2)) STORED' in ddl.create_table_statement(t)


def test_enum_statements(schema):
    stmts = ddl.enum_statements(schema)
    assert len(stmts) == 1
    assert "CREATE TYPE order_status AS ENUM ('pending', 'paid', 'shipped', 'cancelled')" in stmts[0]


def test_unique_index_is_downgraded_when_not_guaranteed(schema):
    plan = plan_generation(schema)
    users_stmts = ddl.index_statements(schema.table("users"), plan)
    assert users_stmts == [
        "CREATE UNIQUE INDEX IF NOT EXISTS users_email_key ON public.users USING btree (email)"
    ]
    # pretend the generator cannot guarantee email uniqueness
    plan.table("users").guaranteed_unique = [["id"]]
    assert ddl.index_statements(schema.table("users"), plan) == [
        "CREATE INDEX IF NOT EXISTS users_email_key ON public.users USING btree (email)"
    ]
    # primary key indexes are never re-created
    assert all("pkey" not in s for s in ddl.index_statements(schema.table("order_items"), plan))


def test_foreign_keys_are_not_valid(schema):
    stmts = ddl.foreign_key_statements(schema.table("orders"), schema)
    assert stmts == [
        'ALTER TABLE "public"."orders" ADD CONSTRAINT "orders_user_id_fkey" FOREIGN KEY ("user_id") '
        'REFERENCES "public"."users" ("id") NOT VALID'
    ]
    assert ddl.drop_foreign_key_statements(schema.table("orders")) == [
        'ALTER TABLE "public"."orders" DROP CONSTRAINT IF EXISTS "orders_user_id_fkey"'
    ]


def test_primary_key_is_named_and_can_be_dropped_for_the_load(schema):
    items = schema.table("order_items")
    sql = ddl.create_table_statement(items)
    assert 'CONSTRAINT "order_items_pkey" PRIMARY KEY ("order_id", "product_id")' in sql
    drop = ddl.drop_primary_key_statement(items)
    add = ddl.add_primary_key_statement(items)
    assert drop is not None and "contype = 'p'" in drop
    assert add is not None and 'PRIMARY KEY ("order_id", "product_id")' in add
    assert "IF NOT EXISTS" in add
    assert ddl.drop_primary_key_statement(Table(name="t", columns=[])) is None


def test_load_slices_cover_every_row_once():
    from dbscale.adapters.postgres.adapter import load_workers, row_slices

    assert row_slices(0, 4) == []
    assert row_slices(10, 1) == [(1, 10)]
    slices = row_slices(10, 3)
    assert slices[0][0] == 1 and slices[-1][1] == 10
    covered: list[int] = []
    for start, end in slices:
        covered.extend(range(start, end + 1))
    assert covered == list(range(1, 11))
    assert load_workers(999_999, cpus=8) == 1
    assert load_workers(1_000_000, cpus=8) == 2
    assert load_workers(50_000_000, cpus=16) == 4


def test_truncate_and_drop(schema):
    assert ddl.truncate_statement(schema).startswith("TRUNCATE ") and ddl.truncate_statement(schema).endswith(
        " CASCADE"
    )
    assert ddl.drop_index_statements(schema.table("products")) == [
        'DROP INDEX IF EXISTS "public"."products_sku_key"'
    ]
