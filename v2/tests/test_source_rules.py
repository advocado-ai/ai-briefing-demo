"""Infrastructure tests, owned by the orchestrator: tools/check_source_rules.py really catches violations.

A checker that can never fail proves nothing, so each rule has a case that must fail and a neighbouring case
that must pass. The checker runs as a subprocess on a throwaway tree.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[1] / "tools" / "check_source_rules.py"


def run_checker(tmp_path: Path, files: dict[str, str]) -> subprocess.CompletedProcess[str]:
    for rel, text in files.items():
        target = tmp_path / "briefing2" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    return subprocess.run(
        [sys.executable, str(TOOL), "--root", str(tmp_path)], capture_output=True, text=True, check=False
    )


def test_empty_package_passes(tmp_path: Path) -> None:
    result = run_checker(tmp_path, {"__init__.py": '"""x"""\n'})
    assert result.returncode == 0, result.stdout


def test_pure_core_with_dates_and_url_parsing_passes(tmp_path: Path) -> None:
    source = (
        "from datetime import date, timedelta\n"
        "from urllib.parse import urlparse\n"
        "\n"
        "\n"
        "def f(d: date) -> str:\n"
        "    return urlparse('x').path + str(d + timedelta(days=1))\n"
    )
    result = run_checker(tmp_path, {"core/ok.py": source})
    assert result.returncode == 0, result.stdout


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("import os\n", "os"),
        ("import sqlite3\n", "sqlite3"),
        ("from pathlib import Path\n", "pathlib"),
        ("import urllib.request\n", "urllib.request"),
        ("import random\n", "random"),
        ("from datetime import datetime\n\n\ndef f() -> object:\n    return datetime.now()\n", ".now()"),
        ("from datetime import date\n\n\ndef f() -> object:\n    return date.today()\n", ".today()"),
        ("def f() -> None:\n    print('x')\n", "print()"),
        ("def f() -> None:\n    open('x')\n", "open()"),
        ("from briefing2.shell import archive\n", "briefing2.shell"),
        ("from ..shell import archive\n", "shell"),
    ],
    ids=[
        "os",
        "sqlite3",
        "pathlib",
        "urllib-request",
        "random",
        "now",
        "today",
        "print",
        "open",
        "shell-abs",
        "shell-rel",
    ],
)
def test_core_violations_are_caught(tmp_path: Path, source: str, expected: str) -> None:
    result = run_checker(tmp_path, {"core/bad.py": source})
    assert result.returncode == 1
    assert expected in result.stdout


def test_input_output_is_allowed_outside_the_core(tmp_path: Path) -> None:
    result = run_checker(tmp_path, {"shell/io.py": "import os\n\n\ndef f() -> object:\n    return os.getcwd()\n"})
    assert result.returncode == 0, result.stdout


@pytest.mark.parametrize(
    "comment",
    ["# type: ignore", "# noqa: E501", "# pragma: no cover", "# fmt: off", "# mypy: ignore-errors"],
    ids=["type-ignore", "noqa", "no-cover", "fmt-off", "mypy-ignore"],
)
def test_escape_hatch_comments_are_caught_everywhere(tmp_path: Path, comment: str) -> None:
    result = run_checker(tmp_path, {"shell/x.py": f"x = 1  {comment}\n"})
    assert result.returncode == 1
    assert "escape-hatch" in result.stdout


def test_the_word_noqa_inside_a_string_is_not_a_comment(tmp_path: Path) -> None:
    result = run_checker(tmp_path, {"shell/x.py": 'MESSAGE = "# noqa is forbidden here"\n'})
    assert result.returncode == 0, result.stdout
