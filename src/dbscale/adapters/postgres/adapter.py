"""PostgreSQL implementation of the ``DatabaseAdapter`` contract."""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from queue import Empty, Queue
from typing import ClassVar

import psycopg
from psycopg import sql as pgsql

from dbscale.adapters.base import (
    AdapterCapabilities,
    AdapterError,
    DatabaseAdapter,
    ExecutionResult,
    ExplainResult,
    ProgressCallback,
    ServerInfo,
)
from dbscale.adapters.postgres import ddl
from dbscale.adapters.postgres.compat import prepare_sandbox
from dbscale.adapters.postgres.inspector import PostgresInspector
from dbscale.adapters.postgres.plan import parse_explain_json
from dbscale.adapters.postgres.synth import SqlValueCompiler
from dbscale.core.scale import ResolvedScale
from dbscale.core.schema import Schema
from dbscale.generation.plan import GenerationPlan
from dbscale.redact import redact_secrets

# Half a million rows keeps one statement's working set small enough that several
# backends can load at once without pushing a laptop into swap.
_CHUNK_ROWS = 500_000
_PARALLEL_MIN_ROWS = 1_000_000
_MAX_LOAD_WORKERS = 4
# Each parallel CREATE INDEX worker gets its own maintenance_work_mem.
# 2 * 128MB stays inside a small Docker VM; 512MB on one backend did not.
_INDEX_MAINTENANCE_WORKERS = 2
_MAINTENANCE_WORK_MEM = "128MB"


class PostgresAdapter(DatabaseAdapter):
    type: ClassVar[str] = "postgres"
    capabilities: ClassVar[AdapterCapabilities] = AdapterCapabilities(
        execution_plans=True,
        rows_scanned=True,
        index_information=True,
        buffer_statistics=True,
        column_statistics=True,
    )

    def __init__(self, conn: psycopg.Connection, url: str, read_only: bool):
        self.conn = conn
        self.url = url
        self.read_only = read_only
        self._fidelity_notes: list[str] = []

    # ------------------------------------------------------------ lifecycle

    @classmethod
    def connect(cls, url: str, *, read_only: bool = False) -> PostgresAdapter:
        options = "-c default_transaction_read_only=on" if read_only else None
        try:
            conn = psycopg.connect(url, autocommit=True, options=options, connect_timeout=15)
        except psycopg.Error as exc:
            raise AdapterError(f"Could not connect to PostgreSQL: {redact_secrets(str(exc))}") from exc
        if read_only:
            conn.read_only = True
        return cls(conn, url, read_only)

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:
            pass

    # ----------------------------------------------------------- discovery

    def server_info(self) -> ServerInfo:
        with self.conn.cursor() as cur:
            cur.execute("SHOW server_version")
            version = cur.fetchone()[0]  # type: ignore[index]
            cur.execute(
                "SELECT current_database(), current_setting('shared_buffers'), current_setting('work_mem')"
            )
            dbname, shared_buffers, work_mem = cur.fetchone()  # type: ignore[misc]
        return ServerInfo(
            type=self.type,
            version=version,
            details={"database": dbname, "shared_buffers": shared_buffers, "work_mem": work_mem},
        )

    def inspect_schema(
        self,
        schemas: list[str] | None = None,
        include_tables: list[str] | None = None,
        exclude_tables: list[str] | None = None,
        *,
        sample_common_values: bool = False,
    ) -> Schema:
        version = self.server_info().version
        inspector = PostgresInspector(self.conn, version, sample_common_values=sample_common_values)
        return inspector.inspect(schemas, include_tables, exclude_tables)

    # -------------------------------------------------------- reproduction

    def _guard_writable(self) -> None:
        if self.read_only:
            raise AdapterError("Refusing to write through a read-only (source) connection")

    def adapt_to_sandbox(self, schema: Schema) -> list[str]:
        """Install extensions the image provides and rewrite types it does not."""
        self._guard_writable()
        for note in prepare_sandbox(self.conn, schema):
            if note not in self._fidelity_notes:
                self._fidelity_notes.append(note)
        return list(self._fidelity_notes)

    def fidelity_notes(self) -> list[str]:
        return list(self._fidelity_notes)

    def _note(self, note: str) -> None:
        if note not in self._fidelity_notes:
            self._fidelity_notes.append(note)

    def _execute(self, stmt: str) -> None:
        with self.conn.cursor() as cur:
            try:
                cur.execute(stmt)
            except psycopg.Error as exc:
                raise AdapterError(f"{exc}\n  while executing: {stmt[:300]}") from exc

    def _execute_optional(self, stmt: str, label: str) -> bool:
        with self.conn.cursor() as cur:
            try:
                cur.execute(stmt)
            except psycopg.Error as exc:
                lines = str(exc).strip().splitlines()
                self._note(f"{label}: skipped ({lines[0] if lines else 'error'})")
                return False
        return True

    def _run(self, statements: list[str]) -> None:
        for stmt in statements:
            self._execute(stmt)

    def create_schema(self, schema: Schema, *, unlogged: bool = True) -> None:
        self._guard_writable()
        self.adapt_to_sandbox(schema)
        self._run(ddl.schema_statements(schema) + ddl.enum_statements(schema))
        for table in schema.topological_order():
            if table.metadata.get("sandbox_skipped"):
                continue
            stmt = ddl.create_table_statement(table, unlogged=unlogged)
            if not self._execute_optional(stmt, f"{table.name}: table"):
                table.metadata["sandbox_skipped"] = self._fidelity_notes[-1]

    def drop_schema(self, schema: Schema) -> None:
        self._guard_writable()
        stmts = [f"DROP TABLE IF EXISTS {ddl.qualified(t)} CASCADE" for t in schema.tables]
        for enum_stmt in ddl.enum_statements(schema):
            # "CREATE TYPE name AS ENUM" -> drop the type if present
            type_name = enum_stmt.split("CREATE TYPE ", 1)[1].split(" AS ENUM", 1)[0]
            stmts.append(f"DROP TYPE IF EXISTS {type_name}")
        self._run(stmts)

    def create_indexes(self, schema: Schema, plan: GenerationPlan) -> None:
        self._guard_writable()
        self._execute(f"SET maintenance_work_mem = '{_MAINTENANCE_WORK_MEM}'")
        self._execute(f"SET max_parallel_maintenance_workers = {_INDEX_MAINTENANCE_WORKERS}")
        try:
            for table in schema.tables:
                if table.metadata.get("sandbox_skipped"):
                    continue
                # Primary keys are created with the table, dropped for the load, then rebuilt
                # here as one sort instead of a per-row index insert.
                pk = ddl.add_primary_key_statement(table)
                if pk:
                    # Not optional: a failed primary key means the load produced duplicate keys.
                    self._execute(pk)
                for stmt in ddl.index_statements(table, plan):
                    self._execute_optional(stmt, f"{table.name}: index")
        finally:
            self._execute("RESET maintenance_work_mem")
            self._execute("RESET max_parallel_maintenance_workers")

    def create_foreign_keys(self, schema: Schema) -> None:
        self._guard_writable()
        for table in schema.tables:
            if table.metadata.get("sandbox_skipped"):
                continue
            for stmt in ddl.foreign_key_statements(table, schema):
                self._execute_optional(stmt, f"{table.name}: foreign key")

    def drop_indexes(self, schema: Schema) -> None:
        self._guard_writable()
        stmts: list[str] = []
        for table in schema.tables:
            stmts += ddl.drop_index_statements(table)
        self._run(stmts)

    def drop_foreign_keys(self, schema: Schema) -> None:
        self._guard_writable()
        stmts: list[str] = []
        for table in schema.tables:
            stmts += ddl.drop_foreign_key_statements(table)
        self._run(stmts)

    def truncate(self, schema: Schema) -> None:
        self._guard_writable()
        live = [t for t in schema.tables if not t.metadata.get("sandbox_skipped")]
        if live:
            self._run(["TRUNCATE " + ", ".join(ddl.qualified(t) for t in live) + " CASCADE"])

    def populate(
        self,
        schema: Schema,
        plan: GenerationPlan,
        scale: ResolvedScale,
        progress: ProgressCallback | None = None,
    ) -> None:
        self._guard_writable()
        compiler = SqlValueCompiler(schema, plan, scale.rows)
        self._execute("SET synchronous_commit = off")
        self._execute("SET jit = off")
        try:
            for table_plan in plan.tables:
                table = schema.table(table_plan.table)
                if table is None or table.metadata.get("sandbox_skipped"):
                    continue
                total = scale.rows.get(table.name, 0)
                cap = compiler.row_cap(table_plan)
                if cap is not None and total > cap:
                    total = cap
                if total <= 0:
                    continue
                names, exprs = compiler.select_list(table, table_plan, total)
                if not names:
                    continue
                # Heap append only. The primary key is rebuilt in create_indexes.
                drop_pk = ddl.drop_primary_key_statement(table)
                if drop_pk:
                    self._execute(drop_pk)
                insert_head = (
                    f"INSERT INTO {ddl.qualified(table)} ({', '.join(names)}) SELECT {', '.join(exprs)} "
                    "FROM generate_series("
                )
                workers = load_workers(total)
                if workers == 1:
                    _insert_range(self.conn, table.name, insert_head, 1, total, total, progress)
                else:
                    _insert_parallel(self.url, table.name, insert_head, total, workers, progress)
        finally:
            self._execute("RESET synchronous_commit")
            self._execute("RESET jit")

    def analyze(self, schema: Schema) -> None:
        self._guard_writable()
        self._run(
            [f"ANALYZE {ddl.qualified(t)}" for t in schema.tables if not t.metadata.get("sandbox_skipped")]
        )

    def row_counts(self, schema: Schema) -> dict[str, int]:
        counts: dict[str, int] = {}
        with self.conn.cursor() as cur:
            for t in schema.tables:
                if t.metadata.get("sandbox_skipped"):
                    continue
                cur.execute(f"SELECT count(*) FROM {ddl.qualified(t)}")
                counts[t.name] = int(cur.fetchone()[0])  # type: ignore[index]
        return counts

    # ----------------------------------------------------------- execution

    def execute(self, sql: str, *, timeout_ms: int) -> ExecutionResult:
        sql = _strip(sql)
        with self.conn.cursor() as cur:
            cur.execute(pgsql.SQL("SET statement_timeout = {}").format(pgsql.Literal(int(timeout_ms))))
            start = time.perf_counter()
            try:
                cur.execute(sql)
                rows = cur.fetchall() if cur.description else []
            except psycopg.errors.QueryCanceled as exc:
                raise AdapterError(f"Query exceeded timeout of {timeout_ms} ms") from exc
            except psycopg.Error as exc:
                raise AdapterError(str(exc).strip()) from exc
            elapsed = (time.perf_counter() - start) * 1000.0
        return ExecutionResult(elapsed_ms=elapsed, rows_returned=len(rows))

    def explain(self, sql: str, *, timeout_ms: int) -> ExplainResult:
        sql = _strip(sql)
        with self.conn.cursor() as cur:
            cur.execute(pgsql.SQL("SET statement_timeout = {}").format(pgsql.Literal(int(timeout_ms))))
            try:
                cur.execute("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql)
                payload = cur.fetchone()[0]  # type: ignore[index]
            except psycopg.errors.QueryCanceled as exc:
                raise AdapterError(f"EXPLAIN ANALYZE exceeded timeout of {timeout_ms} ms") from exc
            except psycopg.Error as exc:
                raise AdapterError(str(exc).strip()) from exc
        root, planning_ms, execution_ms = parse_explain_json(payload)
        return ExplainResult(root=root, planning_ms=planning_ms, execution_ms=execution_ms, raw=payload)


def _strip(sql: str) -> str:
    sql = sql.strip()
    while sql.endswith(";"):
        sql = sql[:-1].rstrip()
    return sql


def load_workers(total: int, *, cpus: int | None = None) -> int:
    """How many backends should load this table. One backend below a million rows."""
    if total < _PARALLEL_MIN_ROWS:
        return 1
    available = (os.cpu_count() or 2) if cpus is None else max(1, cpus)
    by_size = max(1, total // _CHUNK_ROWS)
    return max(1, min(_MAX_LOAD_WORKERS, available, by_size))


def row_slices(total: int, workers: int) -> list[tuple[int, int]]:
    """Disjoint inclusive ranges covering ``1..total``. The last slice absorbs the remainder."""
    if total <= 0:
        return []
    workers = max(1, min(workers, total))
    span = total // workers
    slices: list[tuple[int, int]] = []
    start = 1
    for worker in range(workers):
        end = total if worker == workers - 1 else start + span - 1
        if start <= end:
            slices.append((start, end))
        start = end + 1
    return slices


def _insert_parallel(
    url: str,
    table: str,
    insert_head: str,
    total: int,
    workers: int,
    progress: ProgressCallback | None,
) -> None:
    slices = row_slices(total, workers)
    # Workers only enqueue counts. The progress callback runs on this thread (the CLI spinner is not thread-safe).
    added: Queue[int] = Queue()
    done = 0

    def on_added(count: int) -> None:
        added.put(count)

    def drain() -> None:
        nonlocal done
        changed = False
        while True:
            try:
                done += added.get_nowait()
            except Empty:
                break
            changed = True
        if progress is not None and changed:
            progress(table, done, total)

    with ThreadPoolExecutor(max_workers=len(slices)) as pool:
        pending = {
            pool.submit(_insert_range_url, url, table, insert_head, start, end, on_added)
            for start, end in slices
        }
        while pending:
            finished, pending = wait(pending, timeout=0.25, return_when=FIRST_COMPLETED)
            drain()
            for future in finished:
                future.result()
        drain()


def _insert_range_url(
    url: str,
    table: str,
    insert_head: str,
    start: int,
    end: int,
    on_added: Callable[[int], None],
) -> None:
    try:
        with psycopg.connect(url, autocommit=True, connect_timeout=15) as conn:
            with conn.cursor() as cur:
                cur.execute("SET synchronous_commit = off")
                cur.execute("SET jit = off")
            _insert_range(conn, table, insert_head, start, end, end - start + 1, None, on_added=on_added)
    except psycopg.Error as exc:
        raise AdapterError(f"Failed populating {table}: {exc}") from exc


def _insert_range(
    conn: psycopg.Connection,
    table: str,
    insert_head: str,
    start: int,
    end: int,
    total: int,
    progress: ProgressCallback | None,
    on_added: Callable[[int], None] | None = None,
) -> None:
    """Insert ``start..end`` inclusive. ``total`` is the whole table, used for progress."""
    # Bounds are trusted integers. Expressions may contain '%' so this is never a parameterized query.
    done = start - 1
    with conn.cursor() as cur:
        while done < end:
            chunk_start = done + 1
            chunk_end = min(end, done + _CHUNK_ROWS)
            try:
                cur.execute(insert_head + f"{int(chunk_start)}, {int(chunk_end)}) AS g(i)")
            except psycopg.Error as exc:
                raise AdapterError(f"Failed populating {table}: {exc}") from exc
            added = chunk_end - done
            done = chunk_end
            if on_added is not None:
                on_added(added)
            elif progress is not None:
                progress(table, done, total)
