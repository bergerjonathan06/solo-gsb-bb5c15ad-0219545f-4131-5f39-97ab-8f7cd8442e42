"""FastAPI 应用入口：站点管理、三阶互调分析与结果查询。"""

from __future__ import annotations

import json
import sqlite3
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, status

from . import db, intermod, schemas


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时初始化数据库（建表，幂等）
    db.init_db()
    yield


app = FastAPI(
    title="RF Intermodulation Analysis API",
    description="射频三阶互调 (2fa-fb) 分析：站点频率方案管理与互调产物命中分析",
    version="1.0.0",
    lifespan=lifespan,
)


# ----------------------------------------------------------------  helpers


def fetch_site(conn: sqlite3.Connection, site_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
    if row is None:
        return None
    carriers = [
        r["freq_hz"]
        for r in conn.execute(
            "SELECT freq_hz FROM carriers WHERE site_id = ? ORDER BY freq_hz", (site_id,)
        )
    ]
    bands = [
        {"start_hz": r["start_hz"], "end_hz": r["end_hz"]}
        for r in conn.execute(
            "SELECT start_hz, end_hz FROM guard_bands WHERE site_id = ? ORDER BY start_hz, end_hz",
            (site_id,),
        )
    ]
    return {
        "id": row["id"],
        "name": row["name"],
        "scan_start_hz": row["scan_start_hz"],
        "scan_end_hz": row["scan_end_hz"],
        "guard_interval_hz": row["guard_interval_hz"],
        "carriers_hz": carriers,
        "guard_bands": bands,
        "created_at": row["created_at"],
    }


def get_site_or_404(conn: sqlite3.Connection, site_id: int) -> dict:
    site = fetch_site(conn, site_id)
    if site is None:
        raise HTTPException(status_code=404, detail=f"site {site_id} not found")
    return site


def analysis_row_to_out(row: sqlite3.Row) -> dict:
    payload = json.loads(row["result_json"])
    return {
        "analysis_id": row["id"],
        "site_id": row["site_id"],
        "created_at": row["created_at"],
        **payload,
    }


# ----------------------------------------------------------------  routes


@app.get("/", include_in_schema=False)
def root() -> dict:
    return {
        "name": "RF Intermodulation Analysis API",
        "docs": "/docs",
        "health": "/api/health",
    }


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.post(
    "/api/sites",
    status_code=status.HTTP_201_CREATED,
    response_model=schemas.SiteOut,
)
def create_site(
    payload: schemas.SiteCreate,
    conn: sqlite3.Connection = Depends(db.get_db),
) -> dict:
    """创建站点并录入载频、扫描范围、保护频段与保护间隔。

    重复载频、非法频段/扫描范围在请求校验阶段整批拒绝（422）；
    站点、载频、保护频段在同一事务写入，任一失败整体回滚。
    """
    try:
        with conn:  # 事务：全部成功才提交
            cur = conn.execute(
                "INSERT INTO sites (name, scan_start_hz, scan_end_hz, guard_interval_hz)"
                " VALUES (?, ?, ?, ?)",
                (
                    payload.name,
                    payload.scan_start_hz,
                    payload.scan_end_hz,
                    payload.guard_interval_hz,
                ),
            )
            site_id = cur.lastrowid
            conn.executemany(
                "INSERT INTO carriers (site_id, freq_hz) VALUES (?, ?)",
                [(site_id, c) for c in payload.carriers_hz],
            )
            conn.executemany(
                "INSERT INTO guard_bands (site_id, start_hz, end_hz) VALUES (?, ?, ?)",
                [(site_id, b.start_hz, b.end_hz) for b in payload.guard_bands],
            )
    except sqlite3.IntegrityError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"site name '{payload.name}' already exists",
        )
    return get_site_or_404(conn, site_id)


@app.get("/api/sites", response_model=list[schemas.SiteOut])
def list_sites(conn: sqlite3.Connection = Depends(db.get_db)) -> list[dict]:
    """查询全部已保存的频率方案。"""
    ids = [r["id"] for r in conn.execute("SELECT id FROM sites ORDER BY id")]
    return [fetch_site(conn, site_id) for site_id in ids]


@app.get("/api/sites/{site_id}", response_model=schemas.SiteOut)
def get_site(site_id: int, conn: sqlite3.Connection = Depends(db.get_db)) -> dict:
    """按 ID 查询已保存的频率方案。"""
    return get_site_or_404(conn, site_id)


@app.post(
    "/api/sites/{site_id}/analyses",
    status_code=status.HTTP_201_CREATED,
    response_model=schemas.AnalysisOut,
)
def run_analysis(site_id: int, conn: sqlite3.Connection = Depends(db.get_db)) -> dict:
    """对站点执行三阶互调分析并保存结果。

    枚举不同载频的 2fa-fb 产物，过滤扫描范围外结果；同频产物聚合全部
    来源组合；计算到最近载频/保护频段的距离并判定命中；按频率升序返回。
    """
    site = get_site_or_404(conn, site_id)
    products = intermod.analyze(
        carriers_hz=site["carriers_hz"],
        guard_bands=[(b["start_hz"], b["end_hz"]) for b in site["guard_bands"]],
        scan_start_hz=site["scan_start_hz"],
        scan_end_hz=site["scan_end_hz"],
        guard_interval_hz=site["guard_interval_hz"],
    )
    summary = {
        "carrier_count": len(site["carriers_hz"]),
        "product_count": len(products),
        "hit_count": sum(1 for p in products if p["is_hit"]),
    }
    payload = {
        "guard_interval_hz": site["guard_interval_hz"],
        "summary": summary,
        "products": products,
    }
    with conn:
        cur = conn.execute(
            "INSERT INTO analyses (site_id, result_json) VALUES (?, ?)",
            (site_id, json.dumps(payload)),
        )
    row = conn.execute("SELECT * FROM analyses WHERE id = ?", (cur.lastrowid,)).fetchone()
    return analysis_row_to_out(row)


@app.get("/api/sites/{site_id}/analyses", response_model=list[schemas.AnalysisListItem])
def list_analyses(site_id: int, conn: sqlite3.Connection = Depends(db.get_db)) -> list[dict]:
    """列出站点全部分析记录（最新的在前）。"""
    get_site_or_404(conn, site_id)
    rows = conn.execute(
        "SELECT * FROM analyses WHERE site_id = ? ORDER BY id DESC", (site_id,)
    ).fetchall()
    return [
        {
            "analysis_id": r["id"],
            "site_id": r["site_id"],
            "created_at": r["created_at"],
            "summary": json.loads(r["result_json"])["summary"],
        }
        for r in rows
    ]


@app.get("/api/sites/{site_id}/analyses/latest", response_model=schemas.AnalysisOut)
def get_latest_analysis(site_id: int, conn: sqlite3.Connection = Depends(db.get_db)) -> dict:
    """获取站点最近一次分析结果。"""
    get_site_or_404(conn, site_id)
    row = conn.execute(
        "SELECT * FROM analyses WHERE site_id = ? ORDER BY id DESC LIMIT 1", (site_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"site {site_id} has no analyses")
    return analysis_row_to_out(row)


@app.get("/api/analyses/{analysis_id}", response_model=schemas.AnalysisOut)
def get_analysis(analysis_id: int, conn: sqlite3.Connection = Depends(db.get_db)) -> dict:
    """按 ID 查询已保存的分析结果。"""
    row = conn.execute("SELECT * FROM analyses WHERE id = ?", (analysis_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"analysis {analysis_id} not found")
    return analysis_row_to_out(row)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000)
