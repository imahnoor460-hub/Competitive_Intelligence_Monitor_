from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.approval_item import ApprovalItem, ApprovalStatus
from app.models.user import User
from app.models.workspace_member import WorkspaceMember, WorkspaceRole
from app.schemas.approval import DecisionRequest, ApprovalItemResponse
from app.dependencies import (
    get_current_user,
    get_current_workspace,
    require_role,
    require_writable_workspace,
)
from app.services.approval_service import decide, ApprovalItemNotFound, ApprovalAlreadyDecided

router = APIRouter(
    prefix="/workspaces/{workspace_id}/approvals",
    tags=["Approvals"]
)


@router.get(
    "/",
    response_model=list[ApprovalItemResponse]
)
def list_approvals(
    workspace_id: int,
    status: ApprovalStatus | None = None,
    db: Session = Depends(get_db),
    membership: WorkspaceMember = Depends(get_current_workspace)
):

    query = db.query(ApprovalItem).filter(ApprovalItem.workspace_id == workspace_id)

    if status is not None:
        query = query.filter(ApprovalItem.status == status)

    return query.order_by(ApprovalItem.requested_at.desc()).all()


@router.post(
    "/{approval_item_id}/approve",
    response_model=ApprovalItemResponse
)
def approve(
    workspace_id: int,
    approval_item_id: int,
    payload: DecisionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    membership: WorkspaceMember = Depends(require_role(WorkspaceRole.owner, WorkspaceRole.reviewer)),
    _demo: None = Depends(require_writable_workspace("approve items"))
):

    try:
        return decide(
            db, workspace_id, approval_item_id, "approved", current_user.id, payload.notes
        )
    except ApprovalItemNotFound:
        raise HTTPException(status_code=404, detail="Approval item not found")
    except ApprovalAlreadyDecided:
        raise HTTPException(status_code=400, detail="This item has already been decided")


@router.post(
    "/{approval_item_id}/reject",
    response_model=ApprovalItemResponse
)
def reject(
    workspace_id: int,
    approval_item_id: int,
    payload: DecisionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    membership: WorkspaceMember = Depends(require_role(WorkspaceRole.owner, WorkspaceRole.reviewer)),
    _demo: None = Depends(require_writable_workspace("reject items"))
):

    try:
        return decide(
            db, workspace_id, approval_item_id, "rejected", current_user.id, payload.notes
        )
    except ApprovalItemNotFound:
        raise HTTPException(status_code=404, detail="Approval item not found")
    except ApprovalAlreadyDecided:
        raise HTTPException(status_code=400, detail="This item has already been decided")
