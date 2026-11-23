import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import hydraulics as hy  # noqa: E402
from app.config import DEFAULT_BUILDING_FILE, load_building  # noqa: E402

# Worked example of PRD 8.4: weekday midday, all-zone demand at 150 %.
BUSY = {"Z1": 11.8, "Z2": 26.6, "Z3": 6.9}
CV = {"Z1": 0.08, "Z2": 0.08, "Z3": 0.08}


@pytest.fixture(scope="session")
def b():
    return load_building(DEFAULT_BUILDING_FILE)   # tests use the demo building, not a user-edited one


@pytest.fixture
def state(b):
    return hy.initial_state(b)
