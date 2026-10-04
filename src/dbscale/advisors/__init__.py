"""Advisors turn findings into recommendations.

* ``rules`` — deterministic, always on.
* ``llm`` — optional, interprets structured results; never sees the database.
"""

from dbscale.advisors.rules.advisor import RulesAdvisor

__all__ = ["RulesAdvisor"]
