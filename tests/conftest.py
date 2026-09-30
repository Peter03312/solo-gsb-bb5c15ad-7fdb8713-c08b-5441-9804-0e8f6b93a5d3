"""pytest 夹具：每个用例独立临时库 + TestClient。"""

import os
import tempfile

import pytest

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp.name}"
os.environ["SEED_SAMPLE"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from app import db  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture()
def client():
    if os.path.exists(_tmp.name):
        os.remove(_tmp.name)
    for suffix in ("-wal", "-shm"):
        p = _tmp.name + suffix
        if os.path.exists(p):
            os.remove(p)
    db.init_db()
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def dataset_payload():
    def _make(ship=(-250, 250), boxes=None, pairs=None, adj=None, slots=None):
        return {
            "slots": slots or [
                {"slot_id": "A1", "stack": "A", "tier": 0, "max_weight": 30, "longitudinal_arm": 10},
                {"slot_id": "A2", "stack": "A", "tier": 1, "max_weight": 25, "longitudinal_arm": 12},
                {"slot_id": "A3", "stack": "A", "tier": 2, "max_weight": 20, "longitudinal_arm": 14},
                {"slot_id": "B1", "stack": "B", "tier": 0, "max_weight": 30, "longitudinal_arm": -10},
                {"slot_id": "B2", "stack": "B", "tier": 1, "max_weight": 25, "longitudinal_arm": -12},
                {"slot_id": "B3", "stack": "B", "tier": 2, "max_weight": 20, "longitudinal_arm": -14},
            ],
            "containers": boxes if boxes is not None else [
                {"container_no": "C001", "weight": 18, "hazmat_class": "1", "discharge_order": 2},
                {"container_no": "C002", "weight": 10, "hazmat_class": "3", "discharge_order": 1},
                {"container_no": "C003", "weight": 15, "hazmat_class": None, "discharge_order": 2},
                {"container_no": "C004", "weight": 9, "hazmat_class": None, "discharge_order": 1},
            ],
            "incompatible_pairs": pairs if pairs is not None else [
                {"class_a": "1", "class_b": "3"}],
            "adjacencies": adj if adj is not None else [
                {"slot_a": "A1", "slot_b": "B1"},
                {"slot_a": "A2", "slot_b": "B2"},
                {"slot_a": "A3", "slot_b": "B3"},
                {"slot_a": "A1", "slot_b": "A2"},
                {"slot_a": "B1", "slot_b": "B2"},
            ],
            "ship": {"min_moment": ship[0], "max_moment": ship[1]},
        }
    return _make
