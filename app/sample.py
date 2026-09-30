"""演示用示例资料（SEED_SAMPLE=1 且库为空时自动录入）。

布局：两个堆栈 A、B，各 3 层（tier 0 底舱）。相邻关系含舱内左右邻与舱口跨栈邻。
4 个箱，2 类危险品（1/3 不相容），力矩区间 [-250, 250] 吨·米。
"""

from __future__ import annotations

from . import db
from .models import (AdjacencyPairIn, ContainerIn, DatasetUpsert,
                     IncompatiblePairIn, ShipConfigIn, SlotIn)

SAMPLE = DatasetUpsert(
    slots=[
        SlotIn(slot_id="A1", stack="A", tier=0, max_weight=30, longitudinal_arm=10),
        SlotIn(slot_id="A2", stack="A", tier=1, max_weight=25, longitudinal_arm=12),
        SlotIn(slot_id="A3", stack="A", tier=2, max_weight=20, longitudinal_arm=14),
        SlotIn(slot_id="B1", stack="B", tier=0, max_weight=30, longitudinal_arm=-10),
        SlotIn(slot_id="B2", stack="B", tier=1, max_weight=25, longitudinal_arm=-12),
        SlotIn(slot_id="B3", stack="B", tier=2, max_weight=20, longitudinal_arm=-14),
    ],
    containers=[
        ContainerIn(container_no="C001", weight=18, hazmat_class="1", discharge_order=2),
        ContainerIn(container_no="C002", weight=10, hazmat_class="3", discharge_order=1),
        ContainerIn(container_no="C003", weight=15, hazmat_class=None, discharge_order=2),
        ContainerIn(container_no="C004", weight=9, hazmat_class=None, discharge_order=1),
    ],
    incompatible_pairs=[IncompatiblePairIn(class_a="1", class_b="3")],
    adjacencies=[
        AdjacencyPairIn(slot_a="A1", slot_b="B1"),
        AdjacencyPairIn(slot_a="A2", slot_b="B2"),
        AdjacencyPairIn(slot_a="A3", slot_b="B3"),
        AdjacencyPairIn(slot_a="A1", slot_b="A2"),
        AdjacencyPairIn(slot_a="B1", slot_b="B2"),
    ],
    ship=ShipConfigIn(min_moment=-250, max_moment=250),
)


def seed_if_empty() -> bool:
    """库内没有任何槽位与箱时写入示例资料，返回是否执行了写入。"""
    with db.get_conn() as conn:
        n_slots = conn.execute("SELECT COUNT(*) c FROM slots").fetchone()["c"]
        n_boxes = conn.execute("SELECT COUNT(*) c FROM containers").fetchone()["c"]
    if n_slots or n_boxes:
        return False
    from .service import replace_dataset
    replace_dataset(SAMPLE)
    return True
