"""Shared test configuration, owned by the orchestrator.

Hypothesis runs with a fixed seed and no example database, so a property test gives the same verdict on every run
and on every machine, and nothing is written into the working folder. A failing example is printed in the report.
"""

from hypothesis import settings

settings.register_profile("gate", derandomize=True, database=None, max_examples=200, deadline=None)
settings.load_profile("gate")
