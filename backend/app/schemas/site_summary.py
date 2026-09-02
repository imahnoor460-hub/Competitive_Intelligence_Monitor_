from datetime import datetime

from pydantic import BaseModel


class SiteSummaryResponse(BaseModel):
    competitor_id: int
    categories: list[str]
    current_offers: list[str]
    generated_at: datetime | None = None

    class Config:
        from_attributes = True


class SiteSummaryJobResponse(BaseModel):
    id: int
    competitor_id: int
    status: str
    error: str | None = None
    finished_at: datetime | None = None

    class Config:
        from_attributes = True
