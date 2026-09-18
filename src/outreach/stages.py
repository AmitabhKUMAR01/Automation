"""Stage result summary printed by the CLI."""

from __future__ import annotations

from pydantic import BaseModel, Field


class StageResult(BaseModel):
    stage: str
    dry_run: bool = False
    processed: int = 0
    succeeded: int = 0
    skipped: int = 0
    failed: int = 0
    details: list[str] = Field(default_factory=list)

    def line(self) -> str:
        mode = "dry-run" if self.dry_run else "live"
        return (
            f"[{self.stage}/{mode}] processed={self.processed} "
            f"ok={self.succeeded} skipped={self.skipped} failed={self.failed}"
        )
