"""Infrastructure test, owned by the orchestrator: the package skeleton imports.

This one is green from the start by design. The red tests for a module are separate files.
"""


def test_package_imports() -> None:
    import briefing2
    import briefing2.core

    assert briefing2.__doc__
    assert briefing2.core.__doc__
