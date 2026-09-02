from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.workspace import Workspace
from app.models.workspace_member import WorkspaceMember, WorkspaceRole
from app.schemas.workspace import (
    WorkspaceCreate,
    WorkspaceResponse,
    WorkspaceWithRole,
    MemberInvite,
    MemberRoleUpdate,
    MemberResponse,
)
from app.dependencies import (
    is_demo_account,
    get_current_user,
    get_current_workspace,
    require_role,
    require_writable_workspace,
    require_not_demo_user,
)
from app.services.slugify import unique_slug

router = APIRouter(
    prefix="/workspaces",
    tags=["Workspaces"]
)


@router.post(
    "/",
    response_model=WorkspaceResponse
)
def create_workspace(
    workspace: WorkspaceCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _demo: None = Depends(require_not_demo_user("create workspaces"))
):

    new_workspace = Workspace(
        name=workspace.name,
        slug=unique_slug(db, workspace.name)
    )

    db.add(new_workspace)
    db.flush()

    db.add(
        WorkspaceMember(
            workspace_id=new_workspace.id,
            user_id=current_user.id,
            role=WorkspaceRole.owner
        )
    )

    db.commit()
    db.refresh(new_workspace)

    return new_workspace


@router.get(
    "/",
    response_model=list[WorkspaceWithRole]
)
def list_my_workspaces(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):

    rows = (
        db.query(Workspace, WorkspaceMember.role)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .filter(WorkspaceMember.user_id == current_user.id)
        .all()
    )

    # read_only is per caller, not per workspace: the demo workspace is
    # writable by an admin and read-only for the shared demo account, so it
    # cannot be computed from the row alone.
    caller_is_demo = is_demo_account(current_user)

    return [
        WorkspaceWithRole(
            **WorkspaceResponse.model_validate(workspace).model_dump(),
            role=role,
            read_only=bool(workspace.is_demo and caller_is_demo),
        )
        for workspace, role in rows
    ]


@router.get(
    "/{workspace_id}/members",
    response_model=list[MemberResponse]
)
def list_members(
    workspace_id: int,
    db: Session = Depends(get_db),
    membership: WorkspaceMember = Depends(get_current_workspace)
):

    rows = (
        db.query(WorkspaceMember, User)
        .join(User, User.id == WorkspaceMember.user_id)
        .filter(WorkspaceMember.workspace_id == workspace_id)
        .all()
    )

    return [
        MemberResponse(
            id=member.id,
            user_id=user.id,
            email=user.email,
            full_name=user.full_name,
            role=member.role,
            created_at=member.created_at
        )
        for member, user in rows
    ]


@router.post(
    "/{workspace_id}/members",
    response_model=MemberResponse
)
def invite_member(
    workspace_id: int,
    invite: MemberInvite,
    db: Session = Depends(get_db),
    membership: WorkspaceMember = Depends(require_role(WorkspaceRole.owner)),
    _demo: None = Depends(require_writable_workspace("change members"))
):

    user = (
        db.query(User)
        .filter(User.email == invite.email)
        .first()
    )

    # TODO(delivery): once an email connector exists (Phase 9), support
    # inviting an email with no existing account via a pending-invite record.
    if user is None:
        raise HTTPException(
            status_code=404,
            detail="No user with that email exists yet"
        )

    existing = (
        db.query(WorkspaceMember)
        .filter(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == user.id
        )
        .first()
    )

    if existing:
        raise HTTPException(
            status_code=400,
            detail="User is already a member of this workspace"
        )

    new_member = WorkspaceMember(
        workspace_id=workspace_id,
        user_id=user.id,
        role=invite.role
    )

    db.add(new_member)
    db.commit()
    db.refresh(new_member)

    return MemberResponse(
        id=new_member.id,
        user_id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=new_member.role,
        created_at=new_member.created_at
    )


@router.patch(
    "/{workspace_id}/members/{member_id}",
    response_model=MemberResponse
)
def change_member_role(
    workspace_id: int,
    member_id: int,
    update: MemberRoleUpdate,
    db: Session = Depends(get_db),
    membership: WorkspaceMember = Depends(require_role(WorkspaceRole.owner)),
    _demo: None = Depends(require_writable_workspace("change members"))
):

    member = (
        db.query(WorkspaceMember)
        .filter(
            WorkspaceMember.id == member_id,
            WorkspaceMember.workspace_id == workspace_id
        )
        .first()
    )

    if member is None:
        raise HTTPException(
            status_code=404,
            detail="Member not found"
        )

    if member.role == WorkspaceRole.owner and update.role != WorkspaceRole.owner:
        _ensure_not_last_owner(db, workspace_id, member.id)

    member.role = update.role
    db.commit()
    db.refresh(member)

    user = db.query(User).filter(User.id == member.user_id).first()

    return MemberResponse(
        id=member.id,
        user_id=member.user_id,
        email=user.email,
        full_name=user.full_name,
        role=member.role,
        created_at=member.created_at
    )


@router.delete("/{workspace_id}/members/{member_id}")
def remove_member(
    workspace_id: int,
    member_id: int,
    db: Session = Depends(get_db),
    membership: WorkspaceMember = Depends(require_role(WorkspaceRole.owner)),
    _demo: None = Depends(require_writable_workspace("change members"))
):

    member = (
        db.query(WorkspaceMember)
        .filter(
            WorkspaceMember.id == member_id,
            WorkspaceMember.workspace_id == workspace_id
        )
        .first()
    )

    if member is None:
        raise HTTPException(
            status_code=404,
            detail="Member not found"
        )

    if member.role == WorkspaceRole.owner:
        _ensure_not_last_owner(db, workspace_id, member.id)

    db.delete(member)
    db.commit()

    return {
        "message": "Member removed"
    }


def _ensure_not_last_owner(db: Session, workspace_id: int, excluding_member_id: int):
    remaining_owners = (
        db.query(WorkspaceMember)
        .filter(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.role == WorkspaceRole.owner,
            WorkspaceMember.id != excluding_member_id
        )
        .count()
    )

    if remaining_owners == 0:
        raise HTTPException(
            status_code=400,
            detail="A workspace must have at least one owner"
        )
