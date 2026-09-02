from datetime import datetime

from pydantic import BaseModel, EmailStr

from app.models.workspace_member import WorkspaceRole


class WorkspaceCreate(BaseModel):
    name: str


class WorkspaceResponse(BaseModel):
    id: int
    name: str
    slug: str
    # Whether this workspace is the public demo. Factual, and the same for
    # everyone.
    is_demo: bool = False
    created_at: datetime

    class Config:
        from_attributes = True


class WorkspaceWithRole(WorkspaceResponse):
    role: WorkspaceRole
    # Whether *this caller* may not write here — true only for the shared demo
    # account inside the demo workspace, so an admin working on the demo's own
    # data sees false. This is what the UI gates on; `is_demo` alone would hide
    # the admin's own controls. Presentation only: dependencies.py enforces it.
    read_only: bool = False


class MemberInvite(BaseModel):
    email: EmailStr
    role: WorkspaceRole = WorkspaceRole.editor


class MemberRoleUpdate(BaseModel):
    role: WorkspaceRole


class MemberResponse(BaseModel):
    id: int
    user_id: int
    email: str
    full_name: str | None = None
    role: WorkspaceRole
    created_at: datetime

    class Config:
        from_attributes = True
