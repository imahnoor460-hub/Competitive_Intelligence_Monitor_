from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.exc import TimeoutError as SATimeoutError
from app.routers import competitor
from app.core.config import settings
from app.core.errors import (
    UnhandledErrorMiddleware,
    pool_timeout_handler,
    unhandled_exception_handler,
)
from app.routers import auth
from app.routers import change_log
from app.routers import workspace
from app.routers import surfaces
from app.routers import insights
from app.routers import briefings
from app.routers import approvals
from app.routers import audit
from app.routers import battlecards
from app.routers import battlecard_updates
from app.routers import response_library
from app.routers import integrations
from app.routers import company_profiles
from app.routers import exports
from app.routers import gdpr
from app.routers import budget
from app.routers import traffic
from app.routers import own_site
from app.routers import site_summary
from app.routers import category_price
from app.routers import check_runs
from app.scheduler import start_scheduler, stop_scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(lifespan=lifespan)

# Pool exhaustion answers 503 rather than 500 (see pool_timeout_handler); the
# Exception handler is a backstop for anything that escapes above the
# middleware below.
app.add_exception_handler(SATimeoutError, pool_timeout_handler)
app.add_exception_handler(Exception, unhandled_exception_handler)

# Order matters. add_middleware inserts at index 0, so the *last* one added is
# outermost: CORSMiddleware must wrap UnhandledErrorMiddleware, or the JSON
# 500 the latter produces goes out without Access-Control-Allow-Origin and the
# browser reports it as a CORS policy error instead of a 500.
app.add_middleware(UnhandledErrorMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(workspace.router)
app.include_router(competitor.router)
app.include_router(surfaces.router)
app.include_router(change_log.router)
app.include_router(insights.router)
app.include_router(briefings.router)
app.include_router(approvals.router)
app.include_router(audit.router)
app.include_router(battlecards.router)
app.include_router(battlecard_updates.router)
app.include_router(response_library.router)
app.include_router(integrations.router)
app.include_router(company_profiles.router)
app.include_router(exports.router)
app.include_router(gdpr.router)
app.include_router(budget.router)
app.include_router(traffic.router)
app.include_router(own_site.router)
app.include_router(site_summary.router)
app.include_router(category_price.router)
app.include_router(check_runs.router)
@app.get("/")
def home():
    return {
        "status": "running"
    }
