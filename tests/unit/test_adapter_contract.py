import inspect

import pytest

from dbscale.adapters import AdapterError, DatabaseAdapter, available_adapters, get_adapter, register_adapter
from dbscale.adapters.postgres import PostgresAdapter


def test_registry_resolves_postgres_and_aliases():
    assert "postgres" in available_adapters()
    assert get_adapter("postgres") is PostgresAdapter
    assert get_adapter("PostgreSQL") is PostgresAdapter
    assert get_adapter("pg") is PostgresAdapter
    with pytest.raises(AdapterError, match="No adapter"):
        get_adapter("mongodb")


def test_postgres_declares_the_metrics_it_collects():
    caps = PostgresAdapter.capabilities
    assert caps.execution_plans
    assert caps.rows_scanned
    assert caps.index_information
    assert caps.buffer_statistics
    assert caps.column_statistics


def test_default_capabilities_claim_nothing():
    assert not DatabaseAdapter.capabilities.execution_plans
    assert not DatabaseAdapter.capabilities.rows_scanned


def test_postgres_implements_every_contract_method():
    abstract = {
        name
        for name, _ in inspect.getmembers(DatabaseAdapter)
        if getattr(getattr(DatabaseAdapter, name), "__isabstractmethod__", False)
    }
    assert abstract  # sanity: the contract has abstract methods
    assert not PostgresAdapter.__abstractmethods__
    for name in abstract:
        assert callable(getattr(PostgresAdapter, name))


def test_incomplete_adapter_cannot_be_instantiated():
    class Half(DatabaseAdapter):
        type = "half"

        @classmethod
        def connect(cls, url, *, read_only=False):
            return cls()

    with pytest.raises(TypeError):
        Half()


def test_register_custom_adapter():
    class Fake(PostgresAdapter):
        type = "fakedb"

    register_adapter(Fake)
    assert get_adapter("fakedb") is Fake
