# 集装箱船配载方案核算服务

用 Python + FastAPI 构建的集装箱船配载（stowage）方案核算 HTTP 服务。录入船舶槽位、
集装箱资料与安全约束后，服务求解满足**全部**配载约束的完整可行方案，并按规定规则
选出唯一最优方案；方案可携带**资料修订号**进行确认，支持过期拒绝与并发槽位冲突防护。

---

## 1. 业务规则

### 录入资料

| 资料 | 字段 |
|---|---|
| 槽位 | 槽位编号、所属堆栈、层编号（自底向上，0 为底舱）、允许承重、纵向力臂 |
| 相邻关系 | 相邻槽位编号对（对称生效） |
| 集装箱 | 箱号（唯一）、重量、危险类别（普通箱为 `null`）、卸港先后顺序（数字越小越早卸） |
| 不相容对 | 不得相邻的危险类别对（A/B 顺序无关） |
| 全船参数 | 总纵向力矩允许区间 `[min_moment, max_moment]`（吨·米） |

### 配载约束（全部满足才是可行方案）

1. **承重**：每箱只能放入承重不小于箱重的槽位，槽位不得超载；
2. **堆叠支撑**：上层有箱时，其正下方必须有箱（不得悬空），且下层箱**不轻于**上层箱；
3. **卸港顺序**：早卸港箱（`discharge_order` 小）不得压在晚卸港箱的下方；
4. **危险品隔离**：不相容危险类别的两个箱不得位于相邻槽位；
5. **总力矩**：全船总纵向力矩（各箱重量 × 所在槽纵向力臂之和）必须落在允许区间内；
6. **完整性**：所有箱都必须分配到槽位（求解时自动避开已被确认占用的槽位）。

### 选优规则（唯一最优方案）

在所有完整可行方案中：

1. 先选**最大占用层数最小**的方案；
2. 层数相同，再按**箱号自然序**排列各箱槽位，取槽位编号序列字典序最小者
   （自然序：`A2 < A10`）。

### 输出

每箱槽位、全船总力矩、最大占用层数，以及**方案修订号**（库内方案序号）。

### 无解

不存在可行方案时，接口返回 `422 INFEASIBLE` 并说明“无解”，**不保存**任何方案。
搜索量超过节点预算（无法在预算内得出结论）时返回 `503 SOLVER_LIMIT`，同样不保存。

### 非法录入（返回 `400 INVALID_DATA`，详情列出全部问题）

- 重量 / 承重非正数（非法重量）；
- 重复箱号、重复槽位编号、同堆栈同层重复；
- 相邻关系引用不存在的槽位、槽位与自身相邻；
- 不相容类别引用空类别或自身、重复对；
- 力矩区间下限大于上限。

### 确认与并发

- 确认时必须携带该方案所依据的 **槽位资料修订号**与**箱资料修订号**；
  资料已被重新录入（修订号推进）则返回 `409 STALE_REVISION`，**原方案保持不变**；
- 多个确认并发发生时，数据库对槽位占用加唯一约束 + 立即写锁事务，
  冲突方返回 `409 SLOT_OCCUPIED`，绝不产生两个方案占用同一已确认槽位；
- 重复确认同一方案为幂等操作，返回 `200 confirmed`。

---

## 2. 目录结构

```
app/
  config.py     环境变量配置
  db.py         SQLite 建表、修订号、写事务
  errors.py     统一错误响应
  models.py     请求/响应模型
  solver.py     核心求解器（逐档层数 + 回溯/分支限界，字典序取首解）
  service.py    资料校验、求解保存、确认并发控制、查询
  sample.py     内置示例资料
  main.py       FastAPI 路由
examples/       请求 JSON 与一键演示脚本
tests/          求解器与 HTTP API 测试（21 个用例）
Dockerfile, docker-compose.yml, entrypoint.sh
```

---

## 3. 启动

### 方式 A：Docker Compose 一键启动（推荐）

```bash
docker compose up --build
```

启动过程自动完成：安装依赖 → 初始化数据库（建表）→ 写入示例资料
（`SEED_SAMPLE=1` 且库为空时）→ 启动 HTTP 服务。

### 方式 B：本地 Python 启动

```bash
python3 -m pip install -r requirements.txt
# 初始化（建表；SEED_SAMPLE=1 时写入示例资料）
SEED_SAMPLE=1 python3 -c "from app import db, config; db.init_db(seed_sample=config.SEED_SAMPLE)"
# 启动
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### 方式 C：仅初始化不启动（容器场景）

```bash
docker compose run --rm stowage init
```

### 健康检查

```bash
curl http://127.0.0.1:8000/health
# {"status":"ok"}
```

交互式文档（Swagger UI）：<http://127.0.0.1:8000/docs> ，ReDoc：<http://127.0.0.1:8000/redoc>

---

## 4. 服务地址与端口

| 项 | 值 |
|---|---|
| 监听地址 | `0.0.0.0`（可用环境变量 `HOST` 覆盖） |
| 监听端口 | **`8000`**（可用环境变量 `PORT` 覆盖） |
| Compose 映射 | `http://127.0.0.1:8000` |
| 基础 URL | `http://127.0.0.1:8000` |

---

## 5. 配置（环境变量）

| 变量 | 默认值 | 说明 |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./data/stowage.db` | SQLite 连接串，仅支持 `sqlite:///路径` |
| `HOST` | `0.0.0.0` | 监听地址 |
| `PORT` | `8000` | 监听端口 |
| `SEED_SAMPLE` | `0` | 启动/初始化时库为空则自动写入示例资料（compose 置 `1`） |
| `SOLVER_NODE_BUDGET` | `2000000` | 单次求解回溯节点预算，超出返回 503，防止极端算例长时间占用 |

---

## 6. HTTP API

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET`  | `/health` | 健康检查 |
| `PUT`  | `/admin/dataset` | 整套录入/替换槽位、箱、不相容对、相邻关系、力矩区间；推进修订号 |
| `GET`  | `/dataset` | 查看当前资料及 `slots_revision` / `containers_revision` / `ship_revision` |
| `POST` | `/plans/solve` | 核算方案；成功保存草稿并返回方案修订号，无解 `422` 且不保存 |
| `POST` | `/plans/{plan_revision}/confirm` | 确认方案（请求体携带资料修订号） |
| `GET`  | `/plans` | 方案列表（新的在前） |
| `GET`  | `/plans/{plan_revision}` | 查询单个方案 |
| `GET`  | `/occupancies` | 当前全部已确认槽位占用 |

错误响应统一形如：

```json
{ "error": { "code": "INVALID_DATA", "message": "录入资料校验失败", "details": ["..."] } }
```

---

## 7. 请求示例

### 7.1 录入资料

```bash
curl -X PUT http://127.0.0.1:8000/admin/dataset \
  -H "Content-Type: application/json" \
  -d @examples/dataset.json
```

`examples/dataset.json` 摘要：

```json
{
  "slots": [
    {"slot_id": "A1", "stack": "A", "tier": 0, "max_weight": 30, "longitudinal_arm": 10},
    {"slot_id": "A2", "stack": "A", "tier": 1, "max_weight": 25, "longitudinal_arm": 12},
    {"slot_id": "B1", "stack": "B", "tier": 0, "max_weight": 30, "longitudinal_arm": -10},
    {"slot_id": "B2", "stack": "B", "tier": 1, "max_weight": 25, "longitudinal_arm": -12}
  ],
  "containers": [
    {"container_no": "C001", "weight": 18, "hazmat_class": "1", "discharge_order": 2},
    {"container_no": "C002", "weight": 10, "hazmat_class": "3", "discharge_order": 1},
    {"container_no": "C003", "weight": 15, "hazmat_class": null, "discharge_order": 2},
    {"container_no": "C004", "weight": 9,  "hazmat_class": null, "discharge_order": 1}
  ],
  "incompatible_pairs": [{"class_a": "1", "class_b": "3"}],
  "adjacencies": [
    {"slot_a": "A1", "slot_b": "B1"}, {"slot_a": "A2", "slot_b": "B2"},
    {"slot_a": "A1", "slot_b": "A2"}, {"slot_a": "B1", "slot_b": "B2"}
  ],
  "ship": {"min_moment": -250, "max_moment": 250}
}
```

响应：

```json
{"slots_revision": 1, "containers_revision": 1, "ship_revision": 1}
```

### 7.2 求解

```bash
curl -X POST http://127.0.0.1:8000/plans/solve
```

响应（示例数据的最优方案：最大占用层数 2，总力矩 18.0）：

```json
{
  "plan_revision": 1,
  "slots_revision": 1,
  "containers_revision": 1,
  "max_occupied_tier": 2,
  "total_moment": 18.0,
  "assignments": [
    {"container_no": "C001", "slot_id": "A1"},
    {"container_no": "C002", "slot_id": "B2"},
    {"container_no": "C003", "slot_id": "B1"},
    {"container_no": "C004", "slot_id": "A2"}
  ]
}
```

说明：按箱号序列对应的槽位序列为 `[A1, B2, B1, A2]`，
是“最大层数 = 2”前提下槽位编号字典序最小的完整可行方案；
危险品 1 类（C001）与 3 类（C002）分居 A1、B2 不相邻；
C001(18t) 在 C004(9t) 下方、C003(15t) 在 C002(10t) 下方，满足承重与卸港顺序。

### 7.3 确认（携带修订号）

```bash
curl -X POST http://127.0.0.1:8000/plans/1/confirm \
  -H "Content-Type: application/json" \
  -d '{"plan_revision": 1, "slots_revision": 1, "containers_revision": 1}'
```

响应：

```json
{"plan_revision": 1, "status": "confirmed", "occupied_slots": ["A1", "A2", "B1", "B2"]}
```

资料已更新后再用旧修订号确认：

```json
HTTP 409
{"error": {"code": "STALE_REVISION", "message": "槽位或箱资料修订号已过期，确认被拒绝，原方案不变", ...}}
```

并发确认撞上同一槽位：

```json
HTTP 409
{"error": {"code": "SLOT_OCCUPIED", "message": "槽位已被并发确认的其它方案占用，确认被拒绝，原方案不变", ...}}
```

### 7.4 一键演示脚本

```bash
./examples/request_example.sh
# 或指定地址
BASE_URL=http://127.0.0.1:8000 ./examples/request_example.sh
```

### 7.5 非法录入示例

```bash
# 负重量 -> 400 INVALID_DATA
curl -i -X PUT http://127.0.0.1:8000/admin/dataset \
  -H "Content-Type: application/json" \
  --data-binary @<(python3 -c "import json;d=json.load(open('examples/dataset.json'));d['containers'][0]['weight']=-5;print(json.dumps(d))")
```

---

## 8. 求解算法说明

- 按堆栈最大占用层数上界 `cap = 1, 2, …` 逐档搜索，首个有解的档位即给出
  “最大占用层数最小”的最优值；
- 每档内深度优先回溯：箱按箱号自然序处理、候选槽按槽位编号自然序尝试，
  因此找到的第一个解就是该层数下槽位序列字典序最小者；
- 回溯中执行：槽位容量、临时悬空放置（与列内所有已放置上下箱比对承重与卸港顺序，
  最终悬空缺口剪枝保证无洞）、危险品相邻、力矩区间（剩余箱力臂极值做分支限界）等剪枝；
- 已确认占用的槽位作为“固定预配箱”参与全部约束计算，且不会被再分配。

---

## 9. 测试

```bash
python3 -m pip install -r requirements.txt
python3 -m pytest -q
```

覆盖：全部五条配载约束、选优（最小层数 + 字典序 + 自然序编号）、承重被迫升层、
固定占用、非法重量/重复箱号/无效槽位 400、无解 422 且不保存、
修订号过期 409 且原方案不变、并发确认只有一方成功且无重复槽位占用、幂等确认等。
