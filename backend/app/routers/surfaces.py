from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.competitor import Competitor
from app.models.surface import Surface, SurfaceType
from app.models.check_run import CheckRun
from app.models.snapshot import Snapshot
from app.models.workspace_member import WorkspaceMember, WorkspaceRole
from app.schemas.surface import SurfaceCreate, SurfaceResponse
from app.schemas.check_run import CheckRunResponse
from app.schemas.snapshot import SnapshotResponse
from app.dependencies import get_current_workspace, require_role, rate_limit
from app.services.check_service import run_surface_check, FetchError
from app.services.surface_discovery_service import discover_surfaces, normalize_url, SurfaceDiscoveryError
from app.scheduler import schedule_surface, unschedule_surface

router = APIRouter(
    prefix="/workspaces/{workspace_id}/competitors/{competitor_id}/surfaces",
    tags=["Surfaces"]
)


def _get_owned_competitor(db: Session, workspace_id: int, competitor_id: int) -> Competitor:
    competitor = (
        db.query(Competitor)
        .filter(
            Competitor.id == competitor_id,
            Competitor.workspace_id == workspace_id
        )
        .first()
    )

    if not competitor:
        raise HTTPException(status_code=404, detail="Competitor not found")

    return competitor


@router.post(
    "/",
    response_model=SurfaceResponse
)
def create_surface(
    workspace_id: int,
    competitor_id: int,
    surface: SurfaceCreate,
    db: Session = Depends(get_db),
    membership: WorkspaceMember = Depends(require_role(WorkspaceRole.owner, WorkspaceRole.editor))
):

    _get_owned_competitor(db, workspace_id, competitor_id)

    new_surface = Surface(
        competitor_id=competitor_id,
        surface_type=surface.surface_type,
        name=surface.name,
        url=str(surface.url),
        check_frequency=surface.check_frequency,
        capture_visual=surface.capture_visual
    )

    db.add(new_surface)
    db.commit()
    db.refresh(new_surface)

    schedule_surface(new_surface)

    return new_surface


@router.get(
    "/",
    response_model=list[SurfaceResponse]
)
def list_surfaces(
    workspace_id: int,
    competitor_id: int,
    db: Session = Depends(get_db),
    membership: WorkspaceMember = Depends(get_current_workspace)
):

    _get_owned_competitor(db, workspace_id, competitor_id)

    return (
        db.query(Surface)
        .filter(Surface.competitor_id == competitor_id)
        .all()
    )


@router.post(
    "/discover",
    response_model=list[SurfaceResponse]
)
def discover_more_surfaces(
    workspace_id: int,
    competitor_id: int,
    db: Session = Depends(get_db),
    membership: WorkspaceMember = Depends(require_role(WorkspaceRole.owner, WorkspaceRole.editor)),
    _rate_limit: None = Depends(rate_limit("surface-discover"))
):
    """Re-runs nav/footer page discovery against a competitor already being
    tracked, so competitors added before this existed (or whose site has
    grown new pages since) can pick up everything currently on the site
    instead of only what was found at creation time. Reuses one of the
    competitor's existing surface URLs as the seed homepage — preferring
    the `other`-typed one, since that's what create_competitor seeds with
    the homepage itself — then skips any discovered page that's already
    being tracked.
    """

    _get_owned_competitor(db, workspace_id, competitor_id)

    existing_surfaces = (
        db.query(Surface)
        .filter(Surface.competitor_id == competitor_id)
        .all()
    )
    if not existing_surfaces:
        raise HTTPException(
            status_code=400,
            detail="Add at least one page first so its site can be identified"
        )

    seed = next((s for s in existing_surfaces if s.surface_type == SurfaceType.other), existing_surfaces[0])
    seed_url = seed.url

    # Built before the release below rather than after it: expire_on_commit is
    # on, so re-reading .url off these instances post-commit would re-SELECT
    # every one of them. Nothing here depends on the discovery result.
    existing_urls = {
        normalized
        for s in existing_surfaces
        if (normalized := normalize_url(s.url)) is not None
    }

    # discover_surfaces launches a browser with a 60s navigation timeout —
    # hand this request session's pooled connection back before it.
    db.commit()

    try:
        discovered = discover_surfaces(seed_url)
    except SurfaceDiscoveryError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    created: list[Surface] = []
    for surface_type, name, url in discovered:
        normalized = normalize_url(url)
        if normalized is None or normalized in existing_urls:
            continue
        existing_urls.add(normalized)

        new_surface = Surface(
            competitor_id=competitor_id,
            surface_type=surface_type,
            name=name,
            url=url,
        )
        db.add(new_surface)
        db.commit()
        db.refresh(new_surface)
        schedule_surface(new_surface)
        created.append(new_surface)

    return created


@router.delete("/{surface_id}")
def delete_surface(
    workspace_id: int,
    competitor_id: int,
    surface_id: int,
    db: Session = Depends(get_db),
    membership: WorkspaceMember = Depends(require_role(WorkspaceRole.owner, WorkspaceRole.editor))
):

    _get_owned_competitor(db, workspace_id, competitor_id)

    surface = (
        db.query(Surface)
        .filter(Surface.id == surface_id, Surface.competitor_id == competitor_id)
        .first()
    )

    if not surface:
        raise HTTPException(status_code=404, detail="Surface not found")

    unschedule_surface(surface.id)

    db.delete(surface)
    db.commit()

    return {"message": "Surface deleted"}


@router.post("/{surface_id}/check")
def check_surface(
    workspace_id: int,
    competitor_id: int,
    surface_id: int,
    db: Session = Depends(get_db),
    membership: WorkspaceMember = Depends(require_role(WorkspaceRole.owner, WorkspaceRole.editor)),
    _rate_limit: None = Depends(rate_limit("surface-check"))
):

    _get_owned_competitor(db, workspace_id, competitor_id)

    surface = (
        db.query(Surface)
        .filter(Surface.id == surface_id, Surface.competitor_id == competitor_id)
        .first()
    )

    if not surface:
        raise HTTPException(status_code=404, detail="Surface not found")

    try:
        return run_surface_check(db, surface)
    except FetchError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.get(
    "/{surface_id}/snapshot",
    response_model=SnapshotResponse
)
def get_latest_snapshot(
    workspace_id: int,
    competitor_id: int,
    surface_id: int,
    db: Session = Depends(get_db),
    membership: WorkspaceMember = Depends(get_current_workspace)
):
    """Returns this surface's most recently captured page content. Mainly
    for a freshly-checked surface that has no ChangeLog yet (nothing to
    diff against on its first check) — the frontend falls back to this so
    "no changes yet" still shows what's currently on the page instead of
    nothing at all.
    """

    _get_owned_competitor(db, workspace_id, competitor_id)

    surface = (
        db.query(Surface)
        .filter(Surface.id == surface_id, Surface.competitor_id == competitor_id)
        .first()
    )

    if not surface:
        raise HTTPException(status_code=404, detail="Surface not found")

    snapshot = (
        db.query(Snapshot)
        .filter(Snapshot.surface_id == surface_id)
        .order_by(Snapshot.id.desc())
        .first()
    )

    if not snapshot:
        raise HTTPException(status_code=404, detail="No snapshot captured yet")

    return snapshot


@router.get(
    "/{surface_id}/check-runs",
    response_model=list[CheckRunResponse]
)
def list_check_runs(
    workspace_id: int,
    competitor_id: int,
    surface_id: int,
    db: Session = Depends(get_db),
    membership: WorkspaceMember = Depends(get_current_workspace)
):

    _get_owned_competitor(db, workspace_id, competitor_id)

    surface = (
        db.query(Surface)
        .filter(Surface.id == surface_id, Surface.competitor_id == competitor_id)
        .first()
    )

    if not surface:
        raise HTTPException(status_code=404, detail="Surface not found")

    return (
        db.query(CheckRun)
        .filter(CheckRun.surface_id == surface_id)
        .order_by(CheckRun.started_at.desc())
        .all()
    )
