"""Sandbox type compatibility: missing extensions become text, known arrays stay arrays."""

from dbscale.adapters.postgres import ddl
from dbscale.adapters.postgres.compat import adapt_schema
from dbscale.adapters.postgres.synth import SqlValueCompiler
from dbscale.core.schema import DataType, Index, Schema, Table
from dbscale.generation import ValueKind, plan_generation
from helpers import col


def _table(*columns, indexes=None) -> Schema:
    table = Table(
        name="evidence_passages",
        columns=[col("id", nullable=False, regtype_name="integer"), *columns],
        primary_key=["id"],
        indexes=indexes
        or [Index(name="evidence_passages_pkey", columns=["id"], primary=True, method="btree")],
        estimated_rows=10,
    )
    return Schema(database_type="postgres", tables=[table])


def test_missing_extension_type_is_stored_as_text_and_its_index_is_skipped():
    embedding = col(
        "embedding",
        DataType.OTHER,
        native="vector(1024)",
        regtype_name="vector",
        type_extension="vector",
        nullable=False,
    )
    index = Index(
        name="idx_emb",
        columns=["embedding"],
        method="hnsw",
        definition='CREATE INDEX idx_emb ON evidence_passages USING hnsw ("embedding" vector_cosine_ops)',
    )
    schema = _table(
        embedding,
        indexes=[
            Index(name="evidence_passages_pkey", columns=["id"], primary=True, method="btree"),
            index,
        ],
    )
    notes = adapt_schema(
        schema,
        present_types={"integer"},
        present_methods={"btree"},
        failed_extensions={"vector": 'extension "vector" is not available'},
    )

    assert embedding.sandbox_type == "text"
    assert embedding.native_type == "vector(1024)"
    assert "not available" in (embedding.degraded_reason or "")
    assert index.skipped_reason
    assert any("extension vector" in note for note in notes)
    assert any("evidence_passages.embedding" in note for note in notes)
    assert any("idx_emb" in note for note in notes)

    sql = ddl.create_table_statement(schema.table("evidence_passages"))
    assert '"embedding" text NOT NULL' in sql
    assert "vector" not in sql
    plan = plan_generation(schema)
    assert plan.table("evidence_passages").column("embedding").spec.kind == ValueKind.TEXT
    assert ddl.index_statements(schema.table("evidence_passages"), plan) == []
    assert adapt_schema(schema, present_types={"integer"}, present_methods={"btree"}) == []


def test_present_type_without_a_generator_still_degrades_when_not_null():
    embedding = col(
        "embedding",
        DataType.OTHER,
        native="vector(1024)",
        regtype_name="vector",
        nullable=False,
    )
    schema = _table(embedding)
    adapt_schema(schema, present_types={"integer", "vector"}, present_methods={"btree", "hnsw"})
    assert embedding.sandbox_type == "text"
    assert "no synthetic generator" in (embedding.degraded_reason or "")


def test_nullable_extension_type_is_kept_when_the_sandbox_has_it():
    geo = col("geo", DataType.OTHER, native="geometry", regtype_name="geometry")
    schema = _table(geo)
    adapt_schema(schema, present_types={"integer", "geometry"}, present_methods={"btree", "gist"})
    assert geo.sandbox_type == "geometry"
    assert geo.degraded_reason is None


def test_builtin_array_is_generated_and_cast_to_its_native_type():
    ids = col(
        "ids",
        DataType.ARRAY,
        native="uuid[]",
        regtype_name="uuid[]",
        array_element=DataType.UUID,
        nullable=False,
    )
    schema = _table(ids)
    adapt_schema(schema, present_types={"integer", "uuid[]"}, present_methods={"btree"})
    assert ids.sandbox_type == "uuid[]"
    plan = plan_generation(schema)
    spec = plan.table("evidence_passages").column("ids").spec
    assert spec.kind == ValueKind.ARRAY
    assert spec.element is not None and spec.element.kind == ValueKind.UUID
    assert not any("unsupported" in note for note in plan.notes)
    compiler = SqlValueCompiler(schema, plan, {"evidence_passages": 4})
    table = schema.table("evidence_passages")
    expr = compiler.column_expr(table, ids, plan.table("evidence_passages").column("ids"), 4)
    assert "ARRAY[" in expr and "::uuid" in expr and expr.endswith("::uuid[]")


def test_generated_expression_that_needs_a_missing_type_is_dropped():
    embedding = col(
        "embedding",
        DataType.OTHER,
        native="vector(1024)",
        regtype_name="vector",
        nullable=False,
        is_generated=True,
        default="vector_in('0')",
    )
    width = col(
        "width",
        nullable=False,
        regtype_name="integer",
        is_generated=True,
        default="vector_dims(embedding)",
    )
    schema = _table(embedding, width)
    notes = adapt_schema(schema, present_types={"integer"}, present_methods={"btree"})
    assert embedding.sandbox_type == "text" and embedding.is_generated is False
    assert width.is_generated is False and width.sandbox_type == "integer"
    assert any("width" in note and "generated expression dropped" in note for note in notes)
    sql = ddl.create_table_statement(schema.table("evidence_passages"))
    assert "GENERATED ALWAYS" not in sql


def test_known_textual_type_missing_from_the_sandbox_degrades():
    label = col("label", DataType.TEXT, native="citext", regtype_name="citext", nullable=False)
    schema = _table(label)
    adapt_schema(schema, present_types={"integer"}, present_methods={"btree"})
    assert label.sandbox_type == "text"
    assert "citext" in (label.degraded_reason or "")
