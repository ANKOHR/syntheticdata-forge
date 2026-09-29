from pathlib import Path

import pytest

from sdforge.compiler import compile_file


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def retail_spec():
    return ROOT / "examples" / "retail.yaml"


@pytest.fixture
def retail_plan(retail_spec):
    return compile_file(retail_spec)

