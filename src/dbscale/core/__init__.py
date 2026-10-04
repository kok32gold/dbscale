"""Database-independent core models and contracts.

Nothing in this package may import from an adapter, the LLM layer, or any
database driver. Adapters translate into these models; everything downstream
(generation, benchmarking, analysis, advisors, reporting) works only with them.
"""
