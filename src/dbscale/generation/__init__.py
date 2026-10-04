"""Synthetic data generation planning.

The planner decides *what* data should look like (a ``GenerationPlan`` built
from the schema and its privacy-safe statistics). The database adapter decides
*how* to materialize it efficiently inside the sandbox.
"""

from dbscale.generation.plan import (
    ColumnPlan,
    GenerationPlan,
    Picker,
    TablePlan,
    ValueKind,
    ValueSpec,
)
from dbscale.generation.planner import GenerationPlanner, plan_generation

__all__ = [
    "ColumnPlan",
    "GenerationPlan",
    "GenerationPlanner",
    "Picker",
    "TablePlan",
    "ValueKind",
    "ValueSpec",
    "plan_generation",
]
