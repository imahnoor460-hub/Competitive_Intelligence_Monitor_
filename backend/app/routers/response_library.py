from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.response_library import ResponseLibraryItem
from app.models.user import User
from app.models.workspace_member import WorkspaceMember, WorkspaceRole
from app.schemas.response_library import (
    ResponseLibraryItemCreate, ResponseLibraryItemUpdate, ResponseLibraryItemResponse
)
from app.dependencies import (
    get_current_user,
    get_current_workspace,
    require_role,
    require_writable_workspace,
)

router = APIRouter(
    prefix="/workspaces/{workspace_id}/response-library",
    tags=["Response Library"]
)


@router.post(
    "/",
    response_model=ResponseLibraryItemResponse
)
def create_item(
    workspace_id: int,
    payload: ResponseLibraryItemCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    membership: WorkspaceMember = Depends(require_role(WorkspaceRole.owner, WorkspaceRole.editor)),
    _demo: None = Depends(require_writable_workspace("edit the response library"))
):

    item = ResponseLibraryItem(
        workspace_id=workspace_id,
        competitor_id=payload.competitor_id,
        title=payload.title,
        body_markdown=payload.body_markdown,
        tags=payload.tags,
        created_by_user_id=current_user.id,
    )
    db.add(item)
    db.commit()
    db.refresh(item)

    return item


@router.get(
    "/",
    response_model=list[ResponseLibraryItemResponse]
)
def list_items(
    workspace_id: int,
    competitor_id: int | None = None,
    tag: str | None = None,
    db: Session = Depends(get_db),
    membership: WorkspaceMember = Depends(get_current_workspace)
):

    query = db.query(ResponseLibraryItem).filter(ResponseLibraryItem.workspace_id == workspace_id)

    if competitor_id is not None:
        query = query.filter(ResponseLibraryItem.competitor_id == competitor_id)

    items = query.order_by(ResponseLibraryItem.created_at.desc()).all()

    if tag is not None:
        # tags is a plain JSON column (no GIN index on it), so filtering by
        # tag is done in Python rather than in SQL — fine at this scale.
        items = [item for item in items if item.tags and tag in item.tags]

    return items


@router.patch(
    "/{item_id}",
    response_model=ResponseLibraryItemResponse
)
def update_item(
    workspace_id: int,
    item_id: int,
    payload: ResponseLibraryItemUpdate,
    db: Session = Depends(get_db),
    membership: WorkspaceMember = Depends(require_role(WorkspaceRole.owner, WorkspaceRole.editor)),
    _demo: None = Depends(require_writable_workspace("edit the response library"))
):

    item = (
        db.query(ResponseLibraryItem)
        .filter(ResponseLibraryItem.id == item_id, ResponseLibraryItem.workspace_id == workspace_id)
        .first()
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Response library item not found")

    if payload.title is not None:
        item.title = payload.title
    if payload.body_markdown is not None:
        item.body_markdown = payload.body_markdown
    if payload.tags is not None:
        item.tags = payload.tags

    db.commit()
    db.refresh(item)

    return item


@router.delete("/{item_id}")
def delete_item(
    workspace_id: int,
    item_id: int,
    db: Session = Depends(get_db),
    membership: WorkspaceMember = Depends(require_role(WorkspaceRole.owner, WorkspaceRole.editor)),
    _demo: None = Depends(require_writable_workspace("edit the response library"))
):

    item = (
        db.query(ResponseLibraryItem)
        .filter(ResponseLibraryItem.id == item_id, ResponseLibraryItem.workspace_id == workspace_id)
        .first()
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Response library item not found")

    db.delete(item)
    db.commit()

    return {"message": "Response library item deleted"}
