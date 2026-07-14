from pydantic import BaseModel, Field
from typing import Optional, Literal
from datetime import datetime


class PaginationQuery(BaseModel):
    page: Optional[int] = None
    paginate: Optional[Literal["true", "false"]] = "false"
    result_per_page: Optional[int] = Field(default=10, le=200)
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    search_keyword: Optional[str] = None
    # is_exported: Optional[Literal["true", "false"]] = None
