"""HTTP 请求与响应模型（Pydantic v2）。

约束分两层处理：
* 结构 / 类型错误（缺字段、类型不符等）由 Pydantic 直接返回 422；
* 业务语义错误（负重量、重复箱号、引用不存在的槽位等）在校收服务里返回 400，
  对应需求中的“非法重量、重复箱号或无效槽位须报错”。
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


# ---------------- 录入资料 ----------------

class SlotIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slot_id: str = Field(..., description="槽位编号，如 A01")
    stack: str = Field(..., description="堆栈（列）编号")
    tier: int = Field(..., ge=0, description="自底向上的层编号，0 为底舱")
    max_weight: float = Field(..., description="该槽允许承重（吨）")
    longitudinal_arm: float = Field(..., description="纵向力臂（米，相对力矩参考点）")


class ContainerIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    container_no: str = Field(..., description="箱号，全局唯一")
    weight: float = Field(..., description="箱重（吨），必须为正数")
    hazmat_class: Optional[str] = Field(None, description="危险类别；普通箱为 null")
    discharge_order: int = Field(..., ge=1, description="卸港先后顺序，数字越小越早卸")


class IncompatiblePairIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    class_a: str
    class_b: str
    # 同一对类别只记一次；A/B 顺序无关


class AdjacencyPairIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slot_a: str
    slot_b: str
    # 相邻槽位对，顺序无关、对称生效


class ShipConfigIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_moment: float = Field(..., description="全船允许总纵向力矩下限（吨·米）")
    max_moment: float = Field(..., description="全船允许总纵向力矩上限（吨·米）")


class DatasetUpsert(BaseModel):
    """一次提交一整套资料；成功后相关资料修订号 +1。"""

    model_config = ConfigDict(extra="forbid")

    slots: list[SlotIn]
    containers: list[ContainerIn]
    incompatible_pairs: list[IncompatiblePairIn]
    adjacencies: list[AdjacencyPairIn] = []
    ship: Optional[ShipConfigIn] = None


# ---------------- 求解 ----------------

class AssignedBox(BaseModel):
    container_no: str
    slot_id: str


class SolveResponse(BaseModel):
    plan_revision: int = Field(..., description="草稿方案修订号（库内方案序号）")
    slots_revision: int
    containers_revision: int
    max_occupied_tier: int = Field(..., description="本方案最大占用层数（自 1 起）")
    total_moment: float
    assignments: list[AssignedBox] = Field(..., description="按箱号升序，每箱一个槽位")


# ---------------- 确认 ----------------

class ConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_revision: int
    slots_revision: int
    containers_revision: int


class ConfirmResponse(BaseModel):
    plan_revision: int
    status: str = Field(..., description="confirmed")
    occupied_slots: list[str]


# ---------------- 查询 ----------------

class SlotOut(BaseModel):
    slot_id: str
    stack: str
    tier: int
    max_weight: float
    longitudinal_arm: float


class ContainerOut(BaseModel):
    container_no: str
    weight: float
    hazmat_class: Optional[str]
    discharge_order: int


class DatasetOut(BaseModel):
    slots_revision: int
    containers_revision: int
    ship_revision: int
    ship: Optional[ShipConfigIn]
    slots: list[SlotOut]
    containers: list[ContainerOut]
    incompatible_pairs: list[IncompatiblePairIn]
    adjacencies: list[AdjacencyPairIn] = []


class PlanOut(BaseModel):
    plan_revision: int
    status: str
    slots_revision: int
    containers_revision: int
    max_occupied_tier: int
    total_moment: float
    assignments: list[AssignedBox]


class OccupancyOut(BaseModel):
    slot_id: str
    plan_revision: int
    container_no: str
