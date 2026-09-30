"""FastAPI 入口：集装箱船配载方案核算服务 HTTP API。"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Query

from . import config, db, service
from .errors import ApiError, api_error_handler
from .models import (ConfirmRequest, ConfirmResponse, DatasetOut,
                     DatasetUpsert, OccupancyOut, PlanOut, SolveResponse)


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db(seed_sample=config.SEED_SAMPLE)
    yield


app = FastAPI(
    title="集装箱船配载方案核算服务",
    version="1.0.0",
    description="录入槽位/箱资料，求解满足全部约束的最优配载方案并支持带修订号的并发确认。",
    lifespan=lifespan,
)
app.add_exception_handler(ApiError, api_error_handler)


@app.get("/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok"}


@app.put("/admin/dataset", response_model=dict, tags=["admin"],
         summary="整套录入/替换槽位、箱、不相容对、相邻关系与力矩区间")
def upsert_dataset(data: DatasetUpsert) -> dict:
    return service.replace_dataset(data)


@app.get("/dataset", response_model=DatasetOut, tags=["admin"], summary="查看当前资料与修订号")
def read_dataset() -> dict:
    return service.get_dataset_out()


@app.post("/plans/solve", response_model=SolveResponse, tags=["plans"],
          summary="核算配载方案：无解返回 422 且不保存")
def solve_plan() -> dict:
    return service.solve_and_store()


@app.post("/plans/{plan_revision}/confirm", response_model=ConfirmResponse,
          tags=["plans"], summary="确认方案：核对资料修订号，过期拒绝，并发槽位冲突拒绝")
def confirm_plan(plan_revision: int, req: ConfirmRequest) -> dict:
    if req.plan_revision != plan_revision:
        raise ApiError(400, "REVISION_MISMATCH",
                       "路径方案修订号与请求体中的 plan_revision 不一致")
    return service.confirm_plan(
        plan_revision, req.slots_revision, req.containers_revision)


@app.get("/plans", response_model=list[PlanOut], tags=["plans"], summary="方案列表（新的在前）")
def plans(limit: int = Query(50, ge=1, le=500)) -> list[dict]:
    return service.list_plans(limit)


@app.get("/plans/{plan_revision}", response_model=PlanOut, tags=["plans"], summary="查询单个方案")
def plan(plan_revision: int) -> dict:
    return service.get_plan(plan_revision)


@app.get("/occupancies", response_model=list[OccupancyOut], tags=["plans"],
         summary="当前全部已确认槽位占用")
def occupancies() -> list[dict]:
    return service.list_occupancies()
