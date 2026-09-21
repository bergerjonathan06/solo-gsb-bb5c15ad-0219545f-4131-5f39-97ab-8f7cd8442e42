"""Pydantic 请求/响应模型。校验失败由 FastAPI 统一返回 422，整批拒绝。"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class GuardBand(BaseModel):
    """保护频段 [start_hz, end_hz]，整数 Hz，start_hz 必须小于 end_hz。"""

    start_hz: int = Field(..., gt=0, description="频段起点，整数 Hz")
    end_hz: int = Field(..., gt=0, description="频段终点，整数 Hz")

    @model_validator(mode="after")
    def check_order(self) -> "GuardBand":
        if self.start_hz >= self.end_hz:
            raise ValueError("guard band requires start_hz < end_hz")
        return self


class SiteCreate(BaseModel):
    """创建站点（频率方案）的请求体。"""

    name: str = Field(..., min_length=1, max_length=128)
    scan_start_hz: int = Field(..., gt=0, description="扫描范围起点，整数 Hz")
    scan_end_hz: int = Field(..., gt=0, description="扫描范围终点，整数 Hz")
    guard_interval_hz: int = Field(..., ge=0, description="保护间隔，整数 Hz")
    carriers_hz: list[int] = Field(..., min_length=2, description="载频列表，整数 Hz，至少 2 个且不得重复")
    guard_bands: list[GuardBand] = Field(default_factory=list, description="保护频段列表")

    @field_validator("carriers_hz")
    @classmethod
    def carriers_must_be_positive_and_unique(cls, value: list[int]) -> list[int]:
        if any(c <= 0 for c in value):
            raise ValueError("carrier frequencies must be positive integers (Hz)")
        if len(set(value)) != len(value):
            raise ValueError("duplicate carrier frequencies are not allowed")
        return value

    @model_validator(mode="after")
    def check_scan_range(self) -> "SiteCreate":
        if self.scan_start_hz >= self.scan_end_hz:
            raise ValueError("scan range requires scan_start_hz < scan_end_hz")
        return self


class SiteOut(BaseModel):
    id: int
    name: str
    scan_start_hz: int
    scan_end_hz: int
    guard_interval_hz: int
    carriers_hz: list[int]
    guard_bands: list[GuardBand]
    created_at: str


class SourceCombination(BaseModel):
    """产生该互调产物的一组来源载频：2*fa_hz - fb_hz。"""

    fa_hz: int
    fb_hz: int


class NearestObject(BaseModel):
    """命中对象：距离产物最近的载频或保护频段。"""

    kind: Literal["carrier", "guard_band"]
    distance_hz: int
    carrier_hz: Optional[int] = None
    band: Optional[GuardBand] = None


class ProductOut(BaseModel):
    frequency_hz: int
    is_hit: bool = Field(..., description="最短距离 < 保护间隔 时判定命中")
    nearest: NearestObject
    distance_to_nearest_carrier_hz: int
    distance_to_nearest_guard_band_hz: Optional[int] = None
    sources: list[SourceCombination]


class AnalysisSummary(BaseModel):
    carrier_count: int
    product_count: int
    hit_count: int


class AnalysisOut(BaseModel):
    analysis_id: int
    site_id: int
    created_at: str
    guard_interval_hz: int
    summary: AnalysisSummary
    products: list[ProductOut]


class AnalysisListItem(BaseModel):
    analysis_id: int
    site_id: int
    created_at: str
    summary: AnalysisSummary
