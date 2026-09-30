"""配载方案求解器。

选优规则（按题目要求）：
1. 在所有“完整可行方案”中，先选最大占用层数最小的方案；
2. 同一最大层数下，按箱号排序后取槽位编号序列字典序最小的方案。

实现方式：按堆栈最大占用层数上界 cap 逐档放开（1..最大栈深），
每档做一次深度优先回溯；箱按箱号自然序处理、候选槽按槽位编号自然序尝试，
因此每档找到的第一个解就是该档下槽位序列字典序最小的解，
而最小可行档即给出全局最优。

可行性约束：
* 槽位不超载；
* 同堆栈自底向上连续，不得悬空；下层箱不轻于上层箱；
* 早卸港箱（discharge_order 小）不得压在晚卸港箱下方；
* 不相容危险类别不得位于相邻槽位；
* 全船总力矩落在允许区间。
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from typing import Iterable, Optional

EPS = 1e-9


def natural_key(text: str) -> list:
    """槽位/箱号自然序：A2 排在 A10 之前。"""
    return [int(part) if part.isdigit() else part.lower()
            for part in re.split(r"(\d+)", text)]


@dataclass(frozen=True)
class Slot:
    slot_id: str
    stack: str
    tier: int
    max_weight: float
    longitudinal_arm: float


@dataclass(frozen=True)
class Container:
    container_no: str
    weight: float
    hazmat_class: Optional[str]
    discharge_order: int


@dataclass
class SolveResult:
    feasible: bool
    reason: Optional[str] = None  # infeasible / solver_limit
    # 箱号 -> 槽位号，按箱号自然序排列
    assignments: dict[str, str] = field(default_factory=dict)
    total_moment: float = 0.0
    max_occupied_tier: int = 0  # 占用到的最高层数（自 1 起；固定箱一并计入）
    nodes_visited: int = 0


class _BudgetExhausted(Exception):
    """回溯节点数超过预算。"""


def solve(
    slots: list[Slot],
    containers: list[Container],
    incompatible: set[frozenset[str]],
    min_moment: float,
    max_moment: float,
    adjacency_pairs: Optional[Iterable[tuple[str, str]]] = None,
    blocked: Optional[dict[str, str]] = None,
    node_budget: int = 2_000_000,
) -> SolveResult:
    """求解入口。

    adjacency_pairs: 相邻槽位编号对，顺序无关、重复无害（录入侧已校验端点有效）。
    blocked: 已确认占用 {槽位号: 箱号}。这些箱视为固定预配，参与承重、悬空、
             卸港、危险品相邻与力矩计算，但其槽位不可再分配。
    """
    sys.setrecursionlimit(10000)
    blocked = dict(blocked or {})

    # ---------- 索引预处理 ----------
    slots = sorted(slots, key=lambda s: natural_key(s.slot_id))
    m = len(slots)
    slot_ids = [s.slot_id for s in slots]
    slot_idx = {sid: k for k, sid in enumerate(slot_ids)}
    caps = [s.max_weight for s in slots]
    arms = [s.longitudinal_arm for s in slots]

    stack_of = [s.stack for s in slots]
    stacks: dict[str, list[int]] = {}
    for k, s in enumerate(slots):
        stacks.setdefault(s.stack, []).append(k)
    for lst in stacks.values():
        lst.sort(key=lambda k: slots[k].tier)
    position = {k: p for lst in stacks.values() for p, k in enumerate(lst)}
    max_depth = max((len(lst) for lst in stacks.values()), default=0)

    adj: list[set[int]] = [set() for _ in range(m)]
    for a, b in (adjacency_pairs or []):
        ia, ib = slot_idx[a], slot_idx[b]
        if ia != ib:
            adj[ia].add(ib)
            adj[ib].add(ia)

    containers = sorted(containers, key=lambda c: natural_key(c.container_no))
    cnos = [c.container_no for c in containers]
    cweight = [c.weight for c in containers]
    chaz = [c.hazmat_class for c in containers]
    cdisc = [c.discharge_order for c in containers]
    cidx_of_no = {no: i for i, no in enumerate(cnos)}

    # ---------- 快速不可行判定 ----------
    blocked_nos = {cno for cno in blocked.values() if cno in cidx_of_no}
    movable = [i for i, no in enumerate(cnos) if no not in blocked_nos]
    if len(movable) > m - len(blocked):
        return SolveResult(False, reason="infeasible")
    for i in movable:
        if cweight[i] > max(caps) + EPS:
            return SolveResult(False, reason="infeasible")

    # ---------- 固定箱（已确认占用）初态 ----------
    occ: dict[int, int] = {}
    fixed_slot: set[int] = set()
    grid: dict[str, list[Optional[int]]] = {
        st: [None] * len(lst) for st, lst in stacks.items()
    }
    base_moment = 0.0
    base_top = 0
    for sid, cno in blocked.items():
        if sid not in slot_idx or cno not in cidx_of_no:
            continue  # 资料已替换产生的陈旧占用，求解时忽略
        s = slot_idx[sid]
        i = cidx_of_no[cno]
        occ[s] = i
        fixed_slot.add(s)
        grid[stack_of[s]][position[s]] = i
        base_moment += cweight[i] * arms[s]
        base_top = max(base_top, position[s] + 1)

    # 无待配箱：零号方案，仅校验力矩区间
    if not movable:
        if min_moment - EPS <= base_moment <= max_moment + EPS:
            return SolveResult(True, assignments={}, total_moment=base_moment,
                               max_occupied_tier=base_top, nodes_visited=1)
        return SolveResult(False, reason="infeasible", nodes_visited=1)

    free_slots = [k for k in range(m) if k not in occ]
    nodes = 0

    def gap_missing_all() -> int:
        """全船各堆栈“已占用最高点以下仍为空”的位数（最终方案必须补齐，否则悬空）。"""
        missing = 0
        for col in grid.values():
            top = -1
            for p in range(len(col) - 1, -1, -1):
                if col[p] is not None:
                    top = p
                    break
            for p in range(top + 1):
                if col[p] is None:
                    missing += 1
        return missing

    def dfs(depth: int, cur_moment: float, cap: int) -> Optional[dict[int, int]]:
        nonlocal nodes
        nodes += 1
        if nodes > node_budget:
            raise _BudgetExhausted

        if depth == len(movable):
            if gap_missing_all() == 0 and min_moment - EPS <= cur_moment <= max_moment + EPS:
                return dict(occ)
            return None

        i = movable[depth]
        rem_count = len(movable) - depth  # 含当前箱
        remaining_after = movable[depth + 1:]

        # 力矩分支限界（松弛：剩余箱一律可取任意自由槽力臂极值）
        if free_slots:
            arm_vals = [arms[s] for s in free_slots]
            amin, amax = min(arm_vals), max(arm_vals)
            lo = hi = cur_moment
            for j in remaining_after:
                lo += cweight[j] * amin
                hi += cweight[j] * amax
            if hi < min_moment - EPS or lo > max_moment + EPS:
                return None

        # 自由槽数量不足直接剪枝；悬空缺口在每次落子后精确检查
        if len(free_slots) < rem_count:
            return None

        for s in free_slots.copy():
            st = stack_of[s]
            p = position[s]

            if p + 1 > cap:  # 层数上界
                continue
            if cweight[i] > caps[s] + EPS:  # 超载
                continue
            col = grid[st]
            # 允许“临时悬空”放置（其下空位最终由缺口剪枝保证补齐）；
            # 因此与本列所有已存在的上、下箱逐一比对承重与卸港顺序。
            ok = True
            for q, jq in enumerate(col):
                if jq is None or q == p:
                    continue
                if q < p:
                    # 下方箱：不轻于当前箱；晚卸（序号大）或同序在下
                    if cweight[jq] + EPS < cweight[i] or cdisc[jq] < cdisc[i]:
                        ok = False
                        break
                else:
                    # 上方箱：不轻于当前箱则当前箱撑不住；早卸箱不得在晚卸箱下方
                    if cweight[i] + EPS < cweight[jq] or cdisc[i] < cdisc[jq]:
                        ok = False
                        break
            if not ok:
                continue

            # 不相容危险类别相邻
            if chaz[i] is not None:
                clash = False
                for t in adj[s]:
                    j = occ.get(t)
                    if j is not None and chaz[j] is not None:
                        if frozenset((chaz[i], chaz[j])) in incompatible:
                            clash = True
                            break
                if clash:
                    continue

            occ[s] = i
            col[p] = i
            free_slots.remove(s)
            # 落子后全船悬空缺口只能由后续箱补齐
            if gap_missing_all() <= len(movable) - depth - 1:
                found = dfs(depth + 1, cur_moment + cweight[i] * arms[s], cap)
            else:
                found = None
            free_slots.append(s)
            col[p] = None
            del occ[s]
            if found is not None:
                return found
        return None

    for cap in range(1, max_depth + 1):
        # 该 cap 下可用于活动箱的槽位（固定箱占掉的不可用）
        cap_slots = sum(1 for k in range(m)
                        if position[k] < cap and k not in fixed_slot)
        if cap_slots < len(movable):
            continue
        nodes = 0
        try:
            assignment = dfs(0, base_moment, cap)
        except _BudgetExhausted:
            return SolveResult(False, reason="solver_limit", nodes_visited=nodes)
        if assignment is not None:
            total = base_moment
            top_tier = base_top
            out: dict[str, str] = {}
            for s, i in assignment.items():
                total += cweight[i] * arms[s]
                top_tier = max(top_tier, position[s] + 1)
                out[cnos[i]] = slot_ids[s]
            ordered = {no: out[no] for no in cnos if no in out}
            return SolveResult(True, assignments=ordered, total_moment=total,
                               max_occupied_tier=top_tier, nodes_visited=nodes)

    return SolveResult(False, reason="infeasible", nodes_visited=nodes)
