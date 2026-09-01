import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from app.base import Base
from app.database import get_db
from app.main import app

# Import all models so Base.metadata knows about every table before create_all.
from app.models.user import User  # noqa: F401
from app.models.competitor import Competitor  # noqa: F401
from app.models.change_log import ChangeLog  # noqa: F401
from app.models.workspace import Workspace  # noqa: F401
from app.models.workspace_member import WorkspaceMember  # noqa: F401
from app.models.surface import Surface  # noqa: F401
from app.models.snapshot import Snapshot  # noqa: F401
from app.models.llm_usage import TokenUsageLog  # noqa: F401
from app.models.check_run import CheckRun  # noqa: F401
from app.models.change_embedding import ChangeEmbedding  # noqa: F401
from app.models.workspace_budget import WorkspaceBudget  # noqa: F401
from app.models.briefing import Briefing  # noqa: F401
from app.models.approval_item import ApprovalItem  # noqa: F401
from app.models.audit_log import AuditLog  # noqa: F401
from app.models.battlecard import Battlecard  # noqa: F401
from app.models.battlecard_update import BattlecardUpdate  # noqa: F401
from app.models.response_library import ResponseLibraryItem  # noqa: F401
from app.models.workspace_integration import WorkspaceIntegration  # noqa: F401
from app.models.company_profile import CompanyProfile  # noqa: F401
from app.models.traffic_snapshot import TrafficSnapshot  # noqa: F401
from app.models.competitor_site_summary import CompetitorSiteSummary  # noqa: F401
from app.models.competitor_discovery_job import CompetitorDiscoveryJob  # noqa: F401
from app.models.check_sweep import CheckSweep  # noqa: F401
from app.services.rate_limiter import reset_rate_limits
from app.services.rendered_content_service import RenderedContentError
from app.services.snapshot import FetchError


@pytest.fixture()
def db_session(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autoflush=False, autocommit=False, bind=engine)

    # Background jobs (briefing generation, battlecard-update proposals) open
    # their own session via a module-level `SessionLocal` rather than the
    # request-scoped get_db dependency (see briefing_service.run_briefing_job
    # / battlecard_service.run_battlecard_update_job) — each importing module
    # binds its own name to the sessionmaker at import time, so patching
    # app.database.SessionLocal alone wouldn't reach them. Point every such
    # consumer at this test's in-memory engine, or BackgroundTasks silently
    # query the real configured DATABASE_URL instead of the test DB.
    import app.database as app_database
    import app.services.briefing_service as briefing_service
    import app.services.battlecard_service as battlecard_service
    import app.services.competitor_discovery_service as competitor_discovery_service
    import app.services.check_service as check_service
    import app.services.job_reconciler as job_reconciler
    monkeypatch.setattr(app_database, "SessionLocal", TestingSessionLocal)
    monkeypatch.setattr(briefing_service, "SessionLocal", TestingSessionLocal)
    monkeypatch.setattr(battlecard_service, "SessionLocal", TestingSessionLocal)
    monkeypatch.setattr(
        competitor_discovery_service, "SessionLocal", TestingSessionLocal
    )
    # check_service gained its own module-level SessionLocal when the check
    # pipeline was split so a worker could execute a queued run out of
    # request scope (execute_surface_check). Without this it opens the real
    # configured DATABASE_URL — which is exactly what the tests here caught
    # the first time the check-all path ran.
    monkeypatch.setattr(check_service, "SessionLocal", TestingSessionLocal)
    monkeypatch.setattr(job_reconciler, "SessionLocal", TestingSessionLocal)

    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def client(db_session, monkeypatch):
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    # The scheduler's start/stop hit the real production DB via SessionLocal
    # directly (not the get_db override above), since app lifespan events
    # aren't part of FastAPI's dependency-injection system. Neutralize them
    # here so tests never touch the real DB or spin up real APScheduler jobs.
    monkeypatch.setattr("app.main.start_scheduler", lambda: None)
    monkeypatch.setattr("app.main.stop_scheduler", lambda: None)

    # Tests must never depend on whether a real NVIDIA_API_KEY happens to be
    # configured in the developer's .env — default every test to "no LLM
    # configured" so the suite stays fast, deterministic, and free. Tests
    # that specifically exercise LLM behavior monkeypatch this again
    # themselves with a fake client, which overrides this default.
    monkeypatch.setattr("app.services.check_service.get_llm_client", lambda: None)

    # Same reasoning for briefing generation, which runs in a queued
    # BackgroundTask (briefing_service.run_briefing_job) and resolves its
    # client from app.services.briefing_service — NOT from the router. A
    # test that patched only routers.briefings.get_llm_client therefore
    # still reached the real provider inside the job: slow, billable, and
    # non-deterministic (the assertion would be made against whatever prose
    # the live model happened to return). Default this to "no LLM" too, so
    # forgetting the job-side patch fails the job loudly instead of quietly
    # going to the network; tests exercising briefing generation patch both
    # names with a fake.
    monkeypatch.setattr("app.services.briefing_service.get_llm_client", lambda: None)

    # And again for battlecard-update proposals, which have the same shape:
    # run_battlecard_update_job is a queued BackgroundTask resolving its
    # client from app.services.battlecard_service, not from the router.
    monkeypatch.setattr("app.services.battlecard_service.get_llm_client", lambda: None)

    # generate_site_summary (triggered automatically after every check that
    # finds new content, and from the manual site-summary refresh) always
    # tries a real Playwright render first. Without a default here, any test
    # that configures a fake LLM client for scoring/embedding would also
    # unknowingly trigger a real, slow browser launch against a fake test
    # domain. Default to "rendering unavailable" (falls back to the stored
    # snapshot, same as a real flaky render) — tests that specifically
    # exercise site-summary/category-price behavior override this themselves
    # with their own fake content.
    def _no_render(url):
        raise RenderedContentError("test default — rendering unavailable")
    monkeypatch.setattr("app.services.site_summary_service.capture_rendered_text", _no_render)
    monkeypatch.setattr("app.services.category_price_service.capture_rendered_text", _no_render)

    # site_summary_service now tries plain HTTP before the browser, so without
    # a default here every site-summary test would make a real requests.get to
    # whatever fake domain it seeded. Default to "the fetch failed", which
    # leaves the stored-snapshot fallback as the path under test; the tests
    # that specifically exercise HTTP-first override this with real text.
    def _no_http(url):
        raise FetchError("test default — plain fetch unavailable")
    monkeypatch.setattr("app.services.site_summary_service.capture_clean_snapshot", _no_http)

    # Rate-limiter state is a module-level dict shared across the whole
    # pytest process, but every test gets a fresh in-memory DB (workspace
    # ids restart at 1 each time) — without resetting here, an unrelated
    # earlier test's hits against "workspace 1" would leak into this one.
    reset_rate_limits()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
