"""业务服务层：资料收存校验、求解、确认并发控制、查询。"""

from __future__ import annotations

import json
import sqlite3
from typing import Optional

from . import db
from .errors import ApiError
from .models import DatasetUpsert
from .solver import Container, Slot, natural_key, solve

INFEASIBLE_STATUS = 422


# ================= 资料收存与校验 =================

def _validate_dataset(data: DatasetUpsert) -> None:
    """整套资料的业务语义校验；发现问题一次性以 400 报错。"""
    problems: list[str] = []

    # ---- 槽位 ----
    seen_ids: set[str] = set()
    seen_pos: set[tuple[str, int]] = set()
    for s in data.slots:
        if not s.slot_id.strip():
            problems.append("存在空槽位编号")
        if s.slot_id in seen_ids:
            problems.append(f"槽位编号重复: {s.slot_id}")
        seen_ids.add(s.slot_id)
        if (s.stack, s.tier) in seen_pos:
            problems.append(f"堆栈 {s.stack} 第 {s.tier} 层槽位重复")
        seen_pos.add((s.stack, s.tier))
        if not s.stack.strip():
            problems.append(f"槽位 {s.slot_id} 的堆栈编号为空")
        if s.max_weight <= 0:
            problems.append(f"槽位 {s.slot_id} 非法承重 {s.max_weight}（须为正数）")

    # ---- 箱 ----
    seen_boxes: set[str] = set()
    classes: set[str] = set()
    for c in data.containers:
        if not c.container_no.strip():
            problems.append("存在空箱号")
        if c.container_no in seen_boxes:
            problems.append(f"重复箱号: {c.container_no}")
        seen_boxes.add(c.container_no)
        if c.weight <= 0:
            problems.append(f"箱 {c.container_no} 非法重量 {c.weight}（须为正数）")
        if c.hazmat_class is not None:
            if not c.hazmat_class.strip():
                problems.append(f"箱 {c.container_no} 危险类别为空串（普通箱请用 null）")
            else:
                classes.add(c.hazmat_class)

    # ---- 不相容类别对 ----
    seen_pairs: set[frozenset[str]] = set()
    for p in data.incompatible_pairs:
        if not p.class_a.strip() or not p.class_b.strip():
            problems.append(f"不相容类别对存在空类别: {p.class_a!r}/{p.class_b!r}")
            continue
        key = frozenset((p.class_a, p.class_b))
        if len(key) == 1:
            problems.append(f"不相容类别对不能引用同一类别: {p.class_a}")
        if key in seen_pairs:
            problems.append(f"不相容类别对重复: {p.class_a}~{p.class_b}")
        seen_pairs.add(key)

    # ---- 相邻槽位对 ----
    seen_adj: set[frozenset[str]] = set()
    for a in data.adjacencies:
        if a.slot_a not in seen_ids or a.slot_b not in seen_ids:
            problems.append(f"相邻关系引用无效槽位: {a.slot_a}~{a.slot_b}")
            continue
        if a.slot_a == a.slot_b:
            problems.append(f"槽位不能与自身相邻: {a.slot_a}")
        key = frozenset((a.slot_a, a.slot_b))
        if key in seen_adj:
            problems.append(f"相邻关系重复: {a.slot_a}~{a.slot_b}")
        seen_adj.add(key)

    # ---- 力矩区间 ----
    if data.ship is not None and data.ship.min_moment > data.ship.max_moment:
        problems.append(
            f"力矩区间非法：下限 {data.ship.min_moment} 大于上限 {data.ship.max_moment}")

    if problems:
        raise ApiError(400, "INVALID_DATA", "录入资料校验失败", problems)


def replace_dataset(data: DatasetUpsert) -> dict[str, int]:
    """整套替换资料并推进资料修订号。"""
    _validate_dataset(data)

    with db.write_tx() as conn:
        revs = db.get_revisions(conn)
        slots_rev = revs["slots_revision"]
        containers_rev = revs["containers_revision"]

        # 槽位连同相邻关系一起替换 -> slots_revision +1
        conn.execute("DELETE FROM adjacency")
        conn.execute("DELETE FROM slots")
        conn.executemany(
            "INSERT INTO slots(slot_id, stack, tier, max_weight, longitudinal_arm)"
            " VALUES (:slot_id, :stack, :tier, :max_weight, :longitudinal_arm)",
            [s.model_dump() for s in data.slots])
        pairs = {frozenset((a.slot_a, a.slot_b)) for a in data.adjacencies}
        conn.executemany(
            "INSERT INTO adjacency(slot_a, slot_b) VALUES (?, ?)",
            [tuple(sorted(k, key=natural_key)) for k in pairs])
        slots_rev = db.bump_revision(conn, "slots_revision")

        # 箱与不相容类别对一起替换 -> containers_revision +1
        conn.execute("DELETE FROM containers")
        conn.execute("DELETE FROM incompatible_pairs")
        conn.executemany(
            "INSERT INTO containers(container_no, weight, hazmat_class, discharge_order)"
            " VALUES (:container_no, :weight, :hazmat_class, :discharge_order)",
            [c.model_dump() for c in data.containers])
        norm_pairs = {frozenset((p.class_a, p.class_b)) for p in data.incompatible_pairs}
        conn.executemany(
            "INSERT INTO incompatible_pairs(class_a, class_b) VALUES (?, ?)",
            [tuple(sorted(k)) for k in norm_pairs])
        containers_rev = db.bump_revision(conn, "containers_revision")

        ship_rev = revs["ship_revision"]
        if data.ship is not None:
            conn.execute("DELETE FROM ship_config")
            conn.execute(
                "INSERT INTO ship_config(id, min_moment, max_moment) VALUES (1, ?, ?)",
                (data.ship.min_moment, data.ship.max_moment))
            ship_rev = db.bump_revision(conn, "ship_revision")

    return {"slots_revision": slots_rev,
            "containers_revision": containers_rev,
            "ship_revision": ship_rev}


# ================= 资料读取 =================

def load_dataset(conn: sqlite3.Connection) -> dict:
    revs = db.get_revisions(conn)
    slot_rows = conn.execute(
        "SELECT * FROM slots ORDER BY slot_id").fetchall()
    slots = [Slot(r["slot_id"], r["stack"], r["tier"],
                  r["max_weight"], r["longitudinal_arm"]) for r in slot_rows]
    c_rows = conn.execute("SELECT * FROM containers ORDER BY container_no").fetchall()
    containers = [Container(r["container_no"], r["weight"],
                            r["hazmat_class"], r["discharge_order"]) for r in c_rows]
    incompat = {frozenset((r["class_a"], r["class_b"]))
                for r in conn.execute("SELECT class_a, class_b FROM incompatible_pairs")}
    adjacency = [(r["slot_a"], r["slot_b"])
                 for r in conn.execute("SELECT slot_a, slot_b FROM adjacency")]
    ship = conn.execute("SELECT min_moment, max_moment FROM ship_config WHERE id = 1").fetchone()
    blocked = {r["slot_id"]: r["container_no"]
               for r in conn.execute("SELECT slot_id, container_no FROM occupancies")}
    return {
        "revs": revs,
        "slots": slots,
        "containers": containers,
        "incompatible": incompat,
        "adjacency": adjacency,
        "ship": ship,
        "blocked": blocked,
    }


# ================= 求解 =================

def solve_and_store() -> dict:
    with db.get_conn() as conn:
        ds = load_dataset(conn)

    ship = ds["ship"]
    if ship is None:
        raise ApiError(400, "NO_SHIP_CONFIG", "尚未录入全船允许力矩区间")
    if not ds["slots"]:
        raise ApiError(400, "NO_SLOTS", "尚未录入任何槽位")

    result = solve(
        slots=ds["slots"],
        containers=ds["containers"],
        incompatible=ds["incompatible"],
        min_moment=ship["min_moment"],
        max_moment=ship["max_moment"],
        adjacency_pairs=ds["adjacency"],
        blocked=ds["blocked"],
    )

    if not result.feasible:
        if result.reason == "solver_limit":
            raise ApiError(503, "SOLVER_LIMIT",
                           "求解搜索量超出节点预算，未得到结论，未保存任何方案",
                           {"nodes_visited": result.nodes_visited})
        # 无解：说明原因且不保存
        raise ApiError(INFEASIBLE_STATUS, "INFEASIBLE",
                       "不存在满足全部约束的完整配载方案（无解，未保存）",
                       {"nodes_visited": result.nodes_visited})

    assignments_json = json.dumps(result.assignments, ensure_ascii=False)
    with db.write_tx() as conn:
        plan_rev = db.next_plan_revision(conn)
        conn.execute(
            "INSERT INTO plans(plan_revision, status, slots_revision, containers_revision,"
            " max_occupied_tier, total_moment, assignments_json)"
            " VALUES (?, 'draft', ?, ?, ?, ?, ?)",
            (plan_rev, ds["revs"]["slots_revision"], ds["revs"]["containers_revision"],
             result.max_occupied_tier, result.total_moment, assignments_json))

    return {
        "plan_revision": plan_rev,
        "slots_revision": ds["revs"]["slots_revision"],
        "containers_revision": ds["revs"]["containers_revision"],
        "max_occupied_tier": result.max_occupied_tier,
        "total_moment": result.total_moment,
        "assignments": [{"container_no": k, "slot_id": v}
                        for k, v in result.assignments.items()],
    }


# ================= 确认 =================

def confirm_plan(plan_revision: int, slots_revision: int,
                 containers_revision: int) -> dict:
    # 库外先读取方案，重锁仅用于与资料/占用做一致性核对
    with db.get_conn() as conn:
        plan = conn.execute(
            "SELECT * FROM plans WHERE plan_revision = ?", (plan_revision,)).fetchone()
    if plan is None:
        raise ApiError(404, "PLAN_NOT_FOUND", f"方案修订号 {plan_revision} 不存在")

    assignments = json.loads(plan["assignments_json"])

    # 冲突重试循环：跨进程确认抢同一槽位时，落败方等待后重试以拿到明确结论
    attempts = 0
    while True:
        attempts += 1
        if attempts > 50:
            raise ApiError(503, "DB_BUSY", "数据库写锁竞争持续，确认未完成，原方案不变")
        try:
            with db.write_tx(immediate=True) as conn:
                cur_revs = db.get_revisions(conn)
                if (cur_revs["slots_revision"] != slots_revision
                        or cur_revs["containers_revision"] != containers_revision
                        or plan["slots_revision"] != slots_revision
                        or plan["containers_revision"] != containers_revision):
                    # 参与的箱与槽位资料已更新：拒绝，原方案保持 draft 不变
                    raise ApiError(409, "STALE_REVISION",
                                   "槽位或箱资料修订号已过期，确认被拒绝，原方案不变",
                                   {"current": {"slots_revision": cur_revs["slots_revision"],
                                                "containers_revision": cur_revs["containers_revision"]},
                                    "submitted": {"slots_revision": slots_revision,
                                                  "containers_revision": containers_revision},
                                    "plan_based_on": {"slots_revision": plan["slots_revision"],
                                                      "containers_revision": plan["containers_revision"]}})

                if plan["status"] == "confirmed":
                    return {"plan_revision": plan_revision, "status": "confirmed",
                            "occupied_slots": sorted(assignments.values(), key=natural_key)}

                # 并发冲突：槽位已被其它已确认方案占用
                wanted = sorted(assignments.values())
                held = {r["slot_id"]: r["plan_revision"] for r in conn.execute(
                    "SELECT slot_id, plan_revision FROM occupancies").fetchall()}
                clashes = [s for s in wanted if s in held]
                if clashes:
                    raise ApiError(409, "SLOT_OCCUPIED",
                                   "槽位已被并发确认的其它方案占用，确认被拒绝，原方案不变",
                                   {"conflicting_slots": clashes,
                                    "occupied_by_plan": {s: held[s] for s in clashes}})

                conn.executemany(
                    "INSERT INTO occupancies(slot_id, plan_revision, container_no)"
                    " VALUES (?, ?, ?)",
                    [(sid, plan_revision, cno) for cno, sid in assignments.items()])
                conn.execute(
                    "UPDATE plans SET status = 'confirmed' WHERE plan_revision = ?",
                    (plan_revision,))
                return {"plan_revision": plan_revision, "status": "confirmed",
                        "occupied_slots": wanted}
        except sqlite3.OperationalError as exc:  # 锁竞争：等待后重试
            if "locked" in str(exc).lower() or "busy" in str(exc).lower():
                continue
            raise
        except sqlite3.IntegrityError:
            # 跨进程竞态下 UNIQUE(slot_id) 的最终防线
            raise ApiError(409, "SLOT_OCCUPIED",
                           "槽位已被并发确认的其它方案占用，确认被拒绝，原方案不变")


# ================= 查询 =================

def get_dataset_out() -> dict:
    with db.get_conn() as conn:
        ds = load_dataset(conn)
        ship_row = ds["ship"]
        return {
            "slots_revision": ds["revs"]["slots_revision"],
            "containers_revision": ds["revs"]["containers_revision"],
            "ship_revision": ds["revs"]["ship_revision"],
            "ship": ({"min_moment": ship_row["min_moment"],
                      "max_moment": ship_row["max_moment"]} if ship_row else None),
            "slots": [s.__dict__ for s in ds["slots"]],
            "containers": [c.__dict__ for c in ds["containers"]],
            "incompatible_pairs": [
                {"class_a": tuple(p)[0], "class_b": tuple(p)[1]}
                for p in sorted((sorted(p) for p in ds["incompatible"]))],
            "adjacencies": [{"slot_a": a, "slot_b": b} for a, b in ds["adjacency"]],
        }


def get_plan(plan_revision: int) -> dict:
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM plans WHERE plan_revision = ?",
                           (plan_revision,)).fetchone()
    if row is None:
        raise ApiError(404, "PLAN_NOT_FOUND", f"方案修订号 {plan_revision} 不存在")
    assignments = json.loads(row["assignments_json"])
    return {
        "plan_revision": row["plan_revision"],
        "status": row["status"],
        "slots_revision": row["slots_revision"],
        "containers_revision": row["containers_revision"],
        "max_occupied_tier": row["max_occupied_tier"],
        "total_moment": row["total_moment"],
        "assignments": [{"container_no": k, "slot_id": v}
                        for k, v in assignments.items()],
    }


def list_plans(limit: int = 50) -> list[dict]:
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT plan_revision FROM plans ORDER BY plan_revision DESC LIMIT ?",
            (limit,)).fetchall()
    return [get_plan(r["plan_revision"]) for r in rows]


def list_occupancies() -> list[dict]:
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT slot_id, plan_revision, container_no FROM occupancies"
            " ORDER BY slot_id").fetchall()
    return [dict(r) for r in rows]
