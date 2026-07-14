from sqlalchemy import Column, Integer, String, Float, Text, DateTime, func

from app.config.database import Base


class SchedulerJobRun(Base):
    """Tracks every scheduled job execution — status, timing, errors and retry count."""

    __tablename__ = "scheduler_job_runs"

    id = Column(Integer, primary_key=True, index=True)

    # Which APScheduler job triggered this run
    job_id   = Column(String(100), nullable=False, index=True)
    job_name = Column(String(255), nullable=False)

    # "running" | "completed" | "failed"
    status = Column(String(50), nullable=False, default="running")

    # Timing
    started_at   = Column(DateTime(timezone=True), nullable=False)
    finished_at  = Column(DateTime(timezone=True), nullable=True)
    duration_sec = Column(Float, nullable=True)

    # Failure details
    error_message = Column(String(1000), nullable=True)
    traceback     = Column(Text, nullable=True)

    # How many times this specific run has been retried (0 = first attempt)
    retry_count = Column(Integer, default=0, nullable=False)

    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
