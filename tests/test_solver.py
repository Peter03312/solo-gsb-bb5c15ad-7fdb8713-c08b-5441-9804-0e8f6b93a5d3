"""求解器约束与选优规则的单元测试。"""

import pytest

from app.solver import Container, Slot, solve


def slots_2x3():
    return [
        Slot("A1", "A", 0, 30, 10), Slot("A2", "A", 1, 25, 12), Slot("A3", "A", 2, 20, 14),
        Slot("B1", "B", 0, 30, -10), Slot("B2", "B", 1, 25, -12), Slot("B3", "B", 2, 20, -14),
    ]


ADJ = [("A1", "B1"), ("A2", "B2"), ("A3", "B3"), ("A1", "A2"), ("B1", "B2")]


def seq(result):
    """槽位编号序列（箱号自然序）。"""
    return list(result.assignments.values())


def test_sample_optimal_min_tier_and_lex_order():
    boxes = [
        Container("C001", 18, "1", 2), Container("C002", 10, "3", 1),
        Container("C003", 15, None, 2), Container("C004", 9, None, 1),
    ]
    r = solve(slots_2x3(), boxes, {frozenset(("1", "3"))}, -250, 250, adjacency_pairs=ADJ)
    assert r.feasible
    assert r.max_occupied_tier == 2  # 4 箱 / 2 栈，最优就是 2 层
    assert seq(r) == ["A1", "B2", "B1", "A2"]
    assert abs(r.total_moment - 18) < 1e-9


def test_lex_prefers_lower_slot_ids_when_tiers_tie():
    # 两个完全相同的普通箱：最优 1 层时应取编号最小的两个底舱槽
    boxes = [Container("C1", 5, None, 1), Container("C2", 5, None, 1)]
    r = solve(slots_2x3(), boxes, set(), -1000, 1000)
    assert r.feasible and r.max_occupied_tier == 1
    assert seq(r) == ["A1", "B1"]


def test_natural_order_slot_numbering():
    # A10 排在 A9 之后（自然序）
    slots = [Slot("A9", "A", 0, 10, 0), Slot("A10", "A", 1, 10, 0)]
    boxes = [Container("C1", 5, None, 1)]
    r = solve(slots, boxes, set(), -1000, 1000)
    assert r.feasible and seq(r) == ["A9"]


def test_heavier_box_must_be_below():
    slots = [Slot("A1", "A", 0, 30, 0), Slot("A2", "A", 1, 30, 0)]
    # 箱号序 C1(重)、C2(轻)：重箱在底、轻箱在顶
    heavy_first = [Container("C1", 20, None, 1), Container("C2", 10, None, 1)]
    r = solve(slots, heavy_first, set(), -1000, 1000)
    assert r.feasible and r.assignments == {"C1": "A1", "C2": "A2"}
    assert r.max_occupied_tier == 2

    # 箱号序换成 X(轻)、Y(重)：尽管箱号 X 在前，承重顺序仍强制 Y 在 A1
    r2 = solve(slots, [Container("X", 10, None, 1), Container("Y", 20, None, 1)],
               set(), -1000, 1000)
    assert r2.feasible and r2.assignments == {"X": "A2", "Y": "A1"}


def test_early_discharge_cannot_be_below_late():
    two = [Slot("A1", "A", 0, 30, 0), Slot("A2", "A", 1, 30, 0)]
    three = two + [Slot("A3", "A", 2, 30, 0)]

    # 同重量排除承重干扰：早卸(1)在上、晚卸(2)在下 => 合法
    ok = solve(two, [Container("E1", 10, None, 1), Container("L1", 10, None, 2)],
               set(), -1000, 1000)
    assert ok.feasible and ok.assignments == {"E1": "A2", "L1": "A1"}

    # 三个箱单栈三层：两个早卸、一个晚卸。晚卸必须在最底，
    # 则另一早卸箱必然位于晚卸箱之上一格、而晚卸箱之上必须全为不更晚的箱——
    # 合法排列应为 E/E/L（自顶向下）。验证求解器确实找到：
    feasible3 = solve(
        three,
        [Container("E1", 10, None, 1), Container("E2", 10, None, 1),
         Container("L1", 10, None, 2)],
        set(), -1000, 1000)
    assert feasible3.feasible
    assert feasible3.assignments["L1"] == "A1"

    # 无解构造：三箱三层竖排。两个早卸箱各 10 吨，但只有顶槽(A3)放得下它们；
    # 底/中槽虽承重 30，但早卸箱进底/中层必然压在晚卸箱 L1(2) 下方。
    # 两个早卸箱无法同时位于“不晚于其下箱”的位置 => 无解
    capped = [Slot("A1", "A", 0, 30, 0), Slot("A2", "A", 1, 30, 0),
              Slot("A3", "A", 2, 12, 0)]
    impossible = solve(
        capped,
        [Container("E1", 10, None, 1), Container("E2", 10, None, 1),
         Container("L1", 8, None, 2)],
        set(), -1000, 1000)
    assert not impossible.feasible


def test_hazmat_incompatible_not_adjacent():
    slots = [
        Slot("A1", "A", 0, 30, 0), Slot("B1", "B", 0, 30, 0),
        Slot("C1", "C", 0, 30, 0),
    ]
    boxes = [Container("C1", 5, "1", 1), Container("C2", 5, "3", 1)]
    # A1、B1 相邻且仅两个槽时 => 无解
    r = solve(slots[:2], boxes, {frozenset(("1", "3"))}, -1000, 1000,
              adjacency_pairs=[("A1", "B1")])
    assert not r.feasible
    # 加入不相邻的 C1 => 可行，且两箱分占 A1、C1
    r2 = solve(slots, boxes, {frozenset(("1", "3"))}, -1000, 1000,
               adjacency_pairs=[("A1", "B1"), ("B1", "C1")])
    assert r2.feasible
    assert set(seq(r2)) == {"A1", "C1"}


def test_overweight_slot_infeasible():
    slots = [Slot("A1", "A", 0, 5, 0)]
    r = solve(slots, [Container("C1", 6, None, 1)], set(), -1000, 1000)
    assert not r.feasible


def test_moment_range_enforced():
    slots = [Slot("A1", "A", 0, 30, 10)]
    boxes = [Container("C1", 5, None, 1)]  # 力矩 = 50
    assert solve(slots, boxes, set(), 0, 50).feasible       # 边界含等号
    assert solve(slots, boxes, set(), 0, 49.999).reason == "infeasible"
    assert solve(slots, boxes, set(), 50.001, 1000).reason == "infeasible"


def test_more_boxes_than_slots_infeasible():
    slots = [Slot("A1", "A", 0, 30, 0)]
    boxes = [Container("C1", 1, None, 1), Container("C2", 1, None, 1)]
    assert not solve(slots, boxes, set(), -1000, 1000).feasible


def test_weight_forces_more_tiers():
    # 两个堆栈各两层共 4 槽，4 个箱。底舱只有 A1/B1 两个，
    # 重箱必须由重箱在其下支撑，两个重箱占满底舱，轻箱升到第二层 => 最优 2 层。
    slots = [
        Slot("A1", "A", 0, 30, 0), Slot("A2", "A", 1, 30, 0),
        Slot("B1", "B", 0, 30, 0), Slot("B2", "B", 1, 30, 0),
    ]
    boxes = [Container("H1", 20, None, 1), Container("H2", 20, None, 1),
             Container("L1", 10, None, 1), Container("L2", 10, None, 1)]
    r = solve(slots, boxes, set(), -1000, 1000)
    assert r.feasible and r.max_occupied_tier == 2
    # 每个堆栈底舱不轻于其上箱
    placed = r.assignments
    weights = {"H1": 20, "H2": 20, "L1": 10, "L2": 10}
    for stack_slots in (("A1", "A2"), ("B1", "B2")):
        bottom, top = stack_slots
        bottom_c = next(c for c, s in placed.items() if s == bottom)
        top_c = next((c for c, s in placed.items() if s == top), None)
        if top_c is not None:
            assert weights[bottom_c] >= weights[top_c]
    # 字典序最小槽位序列
    assert list(placed.values()) == ["A1", "A2", "B1", "B2"]


def test_blocked_slots_are_respected():
    # A1 已被箱 C1（视为固定预配）占用；新箱 C2 只能落到 B1
    slots = slots_2x3()
    boxes = [Container("C1", 5, None, 1), Container("C2", 5, None, 1)]
    r = solve(slots, boxes, set(), -1000, 1000, blocked={"A1": "C1"})
    assert r.feasible
    assert r.assignments["C1"] == "A1" and r.assignments["C2"] == "B1"
    assert r.max_occupied_tier == 1

    # 若所有底舱均被固定占用，活动箱只能上二层
    r2 = solve(slots, boxes, set(), -1000, 1000,
               blocked={"A1": "C1", "B1": "C2"})
    assert r2.feasible and r2.assignments == {}
