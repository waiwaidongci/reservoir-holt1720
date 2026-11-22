from datetime import datetime
from sqlalchemy import JSON, Boolean, Float, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from src.config.database import Base
from src.constants.enums import Task
from src.utils.time import now

class ProbeBatch(Base):
    """一次探头回传批次，以名称和内容指纹去重，重跑同批直接短路。"""
    __tablename__ = 'probe_batches'
    __table_args__ = (UniqueConstraint('name', 'checksum'),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    filename: Mapped[str] = mapped_column(String(160))
    checksum: Mapped[str] = mapped_column(String(64))
    row_count: Mapped[int]
    inserted: Mapped[int] = mapped_column(default=0)
    kept: Mapped[int] = mapped_column(default=0)
    skipped: Mapped[int] = mapped_column(default=0)
    author: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(default=now)

class ProbeReading(Base):
    """分钟读数的全部修订。同池同一分钟仅修订号最大者有效，其余留档核对。"""
    __tablename__ = 'probe_readings'
    __table_args__ = (UniqueConstraint('pool', 'minute', 'revision'),)

    id: Mapped[int] = mapped_column(primary_key=True)
    pool: Mapped[str] = mapped_column(String(40), index=True)
    minute: Mapped[datetime] = mapped_column(index=True)
    level_m: Mapped[float] = mapped_column(Float)
    revision: Mapped[int]
    superseded: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey('probe_batches.id'))
    created_at: Mapped[datetime] = mapped_column(default=now)

class PoolState(Base):
    """每池的归集栅栏：读数或警戒线变化时 epoch 递增。"""
    __tablename__ = 'pool_states'

    pool: Mapped[str] = mapped_column(String(40), primary_key=True)
    threshold_m: Mapped[float] = mapped_column(Float)
    epoch: Mapped[int] = mapped_column(default=0)
    updated_at: Mapped[datetime] = mapped_column(default=now)

class HighEvent(Base):
    """高位事件。重算切换时旧版本置为 superseded 留档，页面只看 active。"""
    __tablename__ = 'high_events'

    id: Mapped[int] = mapped_column(primary_key=True)
    pool: Mapped[str] = mapped_column(String(40), index=True)
    start_at: Mapped[datetime]
    end_at: Mapped[datetime]
    minutes: Mapped[int]
    peak_level: Mapped[float] = mapped_column(Float)
    peak_at: Mapped[datetime]
    threshold_m: Mapped[float] = mapped_column(Float)
    epoch: Mapped[int]
    state: Mapped[str] = mapped_column(String(12), default='active', index=True)
    created_at: Mapped[datetime] = mapped_column(default=now)
    superseded_at: Mapped[datetime | None]

class RecalcJob(Base):
    """受影响区段的重算任务，提交时校验 epoch，晚完成的旧重算直接作废。"""
    __tablename__ = 'recalc_jobs'

    id: Mapped[int] = mapped_column(primary_key=True)
    pool: Mapped[str] = mapped_column(String(40), index=True)
    epoch: Mapped[int]
    threshold_m: Mapped[float] = mapped_column(Float)
    windows: Mapped[list] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default=Task.QUEUED, index=True)
    attempt: Mapped[int] = mapped_column(default=0)
    lease_until: Mapped[datetime | None]
    error: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(default=now)
    finished_at: Mapped[datetime | None]
