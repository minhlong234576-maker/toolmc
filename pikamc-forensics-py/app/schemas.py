"""Pydantic v2 schemas cho request/response."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


# ---------- Investigate ----------
class InvestigateRequest(BaseModel):
    server_id: str = Field(..., min_length=1)
    player_name: str
    player_uuid: str


class EvidenceItem(BaseModel):
    category: str          # xray | fastbreak | combat | movement | inventory | bot
    description: str
    severity: Literal["low", "medium", "high", "critical"]
    value: str


class PlayerMetricsOut(BaseModel):
    play_hours: float = 0.0
    rare_ores_per_hour: float = 0.0
    kdr: float = 0.0
    km_per_hour: float = 0.0
    blocks_per_hour: float = 0.0
    rare_ores_in_inventory: int = 0
    fastbreak_logs_count: int = 0
    movement_violations: int = 0
    mayfly: bool = False
    invulnerable: bool = False
    illegal_items: list[str] = Field(default_factory=list)


class VerdictOut(BaseModel):
    verdict: Literal["CLEAN", "SUSPICIOUS", "CONFIRMED_CHEAT"]
    confidence: int
    evidence: list[EvidenceItem]
    summary: str


class InvestigateResponse(BaseModel):
    id: int | None = None
    server_id: str
    player_name: str
    player_uuid: str
    verdict: str
    confidence: int
    metrics: PlayerMetricsOut
    evidence: list[EvidenceItem]
    summary: str = ""
    created_at: datetime | None = None


# ---------- Scan ----------
class ScanWorldRequest(BaseModel):
    server_id: str = Field(..., min_length=1)


class ScanCreatedResponse(BaseModel):
    scan_id: int
    status: str


class ScanStatusResponse(BaseModel):
    id: int
    server_id: str
    status: str
    progress: int
    total_players: int
    scanned_players: int
    cheat_count: int
    suspicious_count: int
    clean_count: int
    current_player: str | None = None
    created_at: datetime | None = None
    completed_at: datetime | None = None


class ScanResultItem(BaseModel):
    id: int
    scan_id: int
    player_name: str
    player_uuid: str
    verdict: str
    confidence: int
    metrics: PlayerMetricsOut
    evidence: list[EvidenceItem]


# ---------- AI ----------
class AIAnalyzeRequest(BaseModel):
    prompt: str = Field(..., min_length=1)
    scan_id: int | None = None
    server_id: str | None = None
    player_name: str | None = None
    player_uuid: str | None = None
    include_world_data: bool = True
    include_logs: bool = True
    include_player_data: bool = True
    log_limit: int = 50
    provider: Literal["gemini", "openai", "ollama", "meta", "llama", "xai247"] = "gemini"
    model: str | None = None
    temperature: float = 0.3
    strict_read_only: bool | None = None


class AIAnalyzeResponse(BaseModel):
    response: str
    provider: str
    model: str
    duration_ms: int
    strict_read_only: bool


# ---------- History / Stats ----------
class AIHistoryItem(BaseModel):
    id: int
    scan_id: int | None
    prompt: str
    response: str
    provider: str
    model: str
    strict_read_only: bool
    created_at: datetime | None = None


class StatsOverview(BaseModel):
    total_investigations: int
    total_scans: int
    total_cheats_found: int
    cheat_rate: float
    most_common_verdict: str
    last_scan_at: datetime | None = None


class TimelinePoint(BaseModel):
    date: str
    cheat_count: int


class TopOffender(BaseModel):
    player_name: str
    player_uuid: str
    flag_count: int
    max_confidence: int
    worst_verdict: str
    last_seen: datetime | None = None
