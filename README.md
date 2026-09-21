# RF Intermodulation Analysis API（射频互调分析）

基于 **FastAPI + SQLite** 的射频三阶互调（2fa − fb）分析服务：创建站点并录入载频、扫描范围、
保护频段与保护间隔；服务端枚举不同载频的三阶互调产物，聚合同频产物的全部来源组合，
计算产物到最近载频/保护频段的距离并判定命中，结果按频率升序返回并持久化可查。

## 快速启动（单条命令）

```bash
docker compose up --build
```

启动时自动初始化 SQLite 数据库（建表，幂等）。启动后：

| 地址 | 说明 |
| --- | --- |
| http://localhost:8000 | API 根路径 |
| http://localhost:8000/docs | Swagger 交互式文档 |
| http://localhost:8000/api/health | 健康检查 |

服务端口：**8000**（宿主机映射，可在 `docker-compose.yml` 中修改）。
SQLite 数据保存在命名卷 `imd-data`（容器内 `/data/intermod.db`），重启不丢失。

### 本地开发（不用 Docker）

```bash
pip install -r requirements-dev.txt
uvicorn app.main:app --reload --port 8000
pytest            # 运行测试
```

数据库路径默认 `./data/intermod.db`，可用环境变量 `IMD_DB_PATH` 覆盖。

## API 一览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/sites` | 创建站点（载频/扫描范围/保护频段/保护间隔），201 |
| GET | `/api/sites` | 查询全部已保存方案 |
| GET | `/api/sites/{id}` | 按 ID 查询方案 |
| POST | `/api/sites/{id}/analyses` | 执行互调分析并保存结果，201 |
| GET | `/api/sites/{id}/analyses` | 该站点的分析记录列表（最新在前） |
| GET | `/api/sites/{id}/analyses/latest` | 最近一次分析结果 |
| GET | `/api/analyses/{id}` | 按 ID 查询已保存的分析结果 |
| GET | `/api/health` | 健康检查 |

### 示例

```bash
# 1. 创建站点：载频 144/145/146 MHz，扫描 140–150 MHz，保护间隔 100 kHz，
#    保护频段 146.5–147.5 MHz（单位均为整数 Hz）
curl -s -X POST http://localhost:8000/api/sites \
  -H 'Content-Type: application/json' \
  -d '{
        "name": "site-alpha",
        "scan_start_hz": 140000000,
        "scan_end_hz": 150000000,
        "guard_interval_hz": 100000,
        "carriers_hz": [144000000, 145000000, 146000000],
        "guard_bands": [{"start_hz": 146500000, "end_hz": 147500000}]
      }'

# 2. 执行分析（站点 id = 1）
curl -s -X POST http://localhost:8000/api/sites/1/analyses

# 3. 查询已保存方案与结果
curl -s http://localhost:8000/api/sites
curl -s http://localhost:8000/api/sites/1/analyses/latest
```

分析结果中每个产物形如：

```json
{
  "frequency_hz": 147000000,
  "is_hit": true,
  "nearest": {
    "kind": "guard_band",
    "distance_hz": 0,
    "carrier_hz": null,
    "band": {"start_hz": 146500000, "end_hz": 147500000}
  },
  "distance_to_nearest_carrier_hz": 1000000,
  "distance_to_nearest_guard_band_hz": 0,
  "sources": [{"fa_hz": 146000000, "fb_hz": 145000000}]
}
```

## 分析规则

- **枚举**：对所有有序载频对 (fa, fb)（fa ≠ fb，即不同载频）计算三阶产物 `2·fa − fb`。
- **范围过滤**：仅保留落在扫描范围 `[scan_start_hz, scan_end_hz]`（含边界）内的产物。
- **同频聚合**：同一频率的多个来源组合全部聚合到 `sources`（按 (fa, fb) 升序）。
  例如载频 300/400/600 MHz 时，`2·400−600 = 2·300−400 = 200 MHz`，两个组合都会列出。
- **距离**：到载频的距离为 `|f − c|` 的最小值；到保护频段的距离，频段内为 0，
  否则为到最近频段边界的距离。`nearest` 为两者中距离更短的命中对象
  （距离相等时确定性地取载频）。
- **命中判定**：最短距离 **小于** 保护间隔（`distance < guard_interval_hz`）即 `is_hit = true`。
- **排序**：产物按频率升序稳定排序返回。

## 校验与整批拒绝

请求校验失败统一返回 **422**，且不会写入任何数据（整批拒绝）：

- 载频重复、载频非正整数、载频少于 2 个；
- 扫描范围 `scan_start_hz >= scan_end_hz`；
- 保护频段 `start_hz >= end_hz` 或非正整数；
- 保护间隔为负数。

站点写入在单个数据库事务中完成，任一记录失败整体回滚。站点名称唯一，
重名返回 **409**。

## 项目结构

```
app/
  main.py      FastAPI 入口与路由（lifespan 启动时初始化数据库）
  db.py        SQLite 连接与建表
  schemas.py   Pydantic 请求/响应模型与校验
  intermod.py  三阶互调分析核心逻辑（纯函数）
tests/
  test_api.py  API 集成测试
Dockerfile
docker-compose.yml
requirements.txt / requirements-dev.txt
```
