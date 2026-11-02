import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


@pytest.fixture
def demo_case():
    return json.loads((ROOT / "tests/data/b_demo_case.json").read_text(encoding="utf-8"))


@pytest.fixture
def demo_result(demo_case):
    from assurance_ledger.core.ars import ARSCoord
    from assurance_ledger.engine import ARSEngine

    return ARSEngine().score_case(ARSCoord(skill_version=demo_case["skill_version"],
                                          model="synthetic-test", data_profile="20 synthetic claims"),
                                  demo_case)
