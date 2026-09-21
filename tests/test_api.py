"""API 集成测试：使用临时 SQLite 库与 FastAPI TestClient。"""

import pytest
from fastapi.testclient import TestClient

from app.main import app

M = 1_000_000  # MHz -> Hz 简写


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("IMD_DB_PATH", str(tmp_path / "test.db"))
    with TestClient(app) as c:
        yield c


def make_site(**overrides):
    payload = {
        "name": "site-alpha",
        "scan_start_hz": 140 * M,
        "scan_end_hz": 150 * M,
        "guard_interval_hz": 100_000,
        "carriers_hz": [144 * M, 145 * M, 146 * M],
        "guard_bands": [],
    }
    payload.update(overrides)
    return payload


# ------------------------------------------------------------ 站点创建与校验


def test_create_and_get_site(client):
    resp = client.post("/api/sites", json=make_site())
    assert resp.status_code == 201, resp.text
    site = resp.json()
    assert site["id"] == 1
    assert site["carriers_hz"] == [144 * M, 145 * M, 146 * M]
    assert site["guard_interval_hz"] == 100_000

    got = client.get(f"/api/sites/{site['id']}")
    assert got.status_code == 200
    assert got.json()["name"] == "site-alpha"

    listed = client.get("/api/sites")
    assert listed.status_code == 200
    assert [s["name"] for s in listed.json()] == ["site-alpha"]


def test_duplicate_carriers_rejected_as_whole_batch(client):
    resp = client.post("/api/sites", json=make_site(carriers_hz=[144 * M, 145 * M, 144 * M]))
    assert resp.status_code == 422
    # 整批拒绝：数据库中不应留下任何站点
    assert client.get("/api/sites").json() == []


def test_invalid_guard_band_rejected(client):
    bad_band = {"start_hz": 147 * M, "end_hz": 146 * M}  # start >= end
    resp = client.post("/api/sites", json=make_site(guard_bands=[bad_band]))
    assert resp.status_code == 422
    assert client.get("/api/sites").json() == []


def test_invalid_scan_range_rejected(client):
    resp = client.post("/api/sites", json=make_site(scan_start_hz=150 * M, scan_end_hz=140 * M))
    assert resp.status_code == 422
    assert client.get("/api/sites").json() == []


def test_non_positive_carrier_rejected(client):
    resp = client.post("/api/sites", json=make_site(carriers_hz=[144 * M, 0]))
    assert resp.status_code == 422


def test_too_few_carriers_rejected(client):
    resp = client.post("/api/sites", json=make_site(carriers_hz=[144 * M]))
    assert resp.status_code == 422


def test_duplicate_site_name_conflict(client):
    assert client.post("/api/sites", json=make_site()).status_code == 201
    resp = client.post("/api/sites", json=make_site())
    assert resp.status_code == 409


def test_get_missing_site_404(client):
    assert client.get("/api/sites/999").status_code == 404


# ------------------------------------------------------------ 互调分析


def test_analysis_hits_on_carriers(client):
    site_id = client.post("/api/sites", json=make_site()).json()["id"]
    resp = client.post(f"/api/sites/{site_id}/analyses")
    assert resp.status_code == 201, resp.text
    result = resp.json()

    freqs = [p["frequency_hz"] for p in result["products"]]
    # 2fa-fb 全部组合（范围内）：142,143,144,146,147,148 MHz，按频率升序
    assert freqs == [142 * M, 143 * M, 144 * M, 146 * M, 147 * M, 148 * M]
    assert freqs == sorted(freqs)

    by_freq = {p["frequency_hz"]: p for p in result["products"]}
    # 产物恰好落在载频上：距离 0，命中
    hit_144 = by_freq[144 * M]
    assert hit_144["is_hit"] is True
    assert hit_144["nearest"]["kind"] == "carrier"
    assert hit_144["nearest"]["carrier_hz"] == 144 * M
    assert hit_144["nearest"]["distance_hz"] == 0
    assert hit_144["sources"] == [{"fa_hz": 145 * M, "fb_hz": 146 * M}]

    hit_146 = by_freq[146 * M]
    assert hit_146["is_hit"] is True
    assert hit_146["nearest"]["carrier_hz"] == 146 * M

    # 远离载频的产物不命中
    far = by_freq[142 * M]
    assert far["is_hit"] is False
    assert far["distance_to_nearest_carrier_hz"] == 2 * M

    assert result["summary"] == {"carrier_count": 3, "product_count": 6, "hit_count": 2}


def test_same_frequency_products_aggregate_all_sources(client):
    # 2*400-600 = 200 且 2*300-400 = 200：同频产物应聚合两个来源组合
    site_id = client.post(
        "/api/sites",
        json=make_site(
            name="agg",
            scan_start_hz=100 * M,
            scan_end_hz=700 * M,
            guard_interval_hz=0,
            carriers_hz=[300 * M, 400 * M, 600 * M],
        ),
    ).json()["id"]
    result = client.post(f"/api/sites/{site_id}/analyses").json()

    by_freq = {p["frequency_hz"]: p for p in result["products"]}
    # 2*300-600=0、2*600-300=900M、2*600-400=800M 均被范围过滤
    assert sorted(by_freq) == [200 * M, 500 * M]
    agg = by_freq[200 * M]
    assert agg["sources"] == [
        {"fa_hz": 300 * M, "fb_hz": 400 * M},
        {"fa_hz": 400 * M, "fb_hz": 600 * M},
    ]
    assert by_freq[500 * M]["sources"] == [{"fa_hz": 400 * M, "fb_hz": 300 * M}]


def test_guard_band_distance_and_hit(client):
    site_id = client.post(
        "/api/sites",
        json=make_site(guard_bands=[{"start_hz": 146_500_000, "end_hz": 147_500_000}]),
    ).json()["id"]
    result = client.post(f"/api/sites/{site_id}/analyses").json()
    by_freq = {p["frequency_hz"]: p for p in result["products"]}

    # 147M 产物落在保护频段内：到频段距离 0，命中对象为 guard_band
    inside = by_freq[147 * M]
    assert inside["distance_to_nearest_guard_band_hz"] == 0
    assert inside["nearest"]["kind"] == "guard_band"
    assert inside["nearest"]["band"] == {"start_hz": 146_500_000, "end_hz": 147_500_000}
    assert inside["is_hit"] is True

    # 148M 产物在频段外：到频段距离 0.5M，到载频 2M -> 最近对象是保护频段
    outside = by_freq[148 * M]
    assert outside["distance_to_nearest_guard_band_hz"] == 500_000
    assert outside["distance_to_nearest_carrier_hz"] == 2 * M
    assert outside["nearest"]["kind"] == "guard_band"
    assert outside["nearest"]["distance_hz"] == 500_000
    assert outside["is_hit"] is False  # 0.5M >= 保护间隔 100kHz


def test_analysis_saved_and_queryable(client):
    site_id = client.post("/api/sites", json=make_site()).json()["id"]
    first = client.post(f"/api/sites/{site_id}/analyses").json()
    second = client.post(f"/api/sites/{site_id}/analyses").json()
    assert first["analysis_id"] != second["analysis_id"]

    listed = client.get(f"/api/sites/{site_id}/analyses")
    assert listed.status_code == 200
    assert [a["analysis_id"] for a in listed.json()] == [second["analysis_id"], first["analysis_id"]]
    assert listed.json()[0]["summary"]["product_count"] == 6

    latest = client.get(f"/api/sites/{site_id}/analyses/latest")
    assert latest.status_code == 200
    assert latest.json()["analysis_id"] == second["analysis_id"]
    assert len(latest.json()["products"]) == 6

    by_id = client.get(f"/api/analyses/{first['analysis_id']}")
    assert by_id.status_code == 200
    assert by_id.json()["site_id"] == site_id

    assert client.get("/api/analyses/999").status_code == 404
    assert client.get(f"/api/sites/{site_id}/analyses/999").status_code in (404, 405)


def test_analysis_on_missing_site_404(client):
    assert client.post("/api/sites/999/analyses").status_code == 404


def test_health(client):
    assert client.get("/api/health").json() == {"status": "ok"}
