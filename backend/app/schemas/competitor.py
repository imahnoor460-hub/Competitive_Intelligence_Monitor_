from datetime import datetime

from pydantic import BaseModel, HttpUrl

from app.models.competitor_discovery_job import CompetitorDiscoveryJobStatus


class CompetitorCreate(BaseModel):
    name: str
    website_url: HttpUrl | None = None


class CompetitorResponse(BaseModel):
    id: int
    name: str
    is_own_site: bool = False
    created_at: datetime | None = None
    surfaces_discovered: int = 0
    # Set only by create_competitor, and only when a website_url was given.
    # Page discovery runs as a background job now, so the create response
    # carries the job to poll rather than a finished count — see
    # CompetitorDiscoveryJob. Null on every other endpoint returning a
    # competitor.
    discovery_job_id: int | None = None

    class Config:
        from_attributes = True


class CompetitorDiscoveryJobResponse(BaseModel):
    id: int
    status: CompetitorDiscoveryJobStatus
    surfaces_discovered: int = 0
    error: str | None = None
    created_at: datetime
    finished_at: datetime | None = None

    class Config:
        from_attributes = True
