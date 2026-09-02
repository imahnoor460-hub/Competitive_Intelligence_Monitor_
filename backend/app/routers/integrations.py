from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.workspace_integration import WorkspaceIntegration, IntegrationProvider
from app.models.workspace_member import WorkspaceMember, WorkspaceRole
from app.schemas.workspace_integration import (
    WorkspaceIntegrationUpsert, WorkspaceIntegrationResponse, TestSendResponse
)
from app.dependencies import (
    require_role,
    require_writable_workspace,
)
from app.services.delivery.base import DeliveryPayload
from app.services.delivery.delivery_service import get_connector

router = APIRouter(
    prefix="/workspaces/{workspace_id}/integrations",
    tags=["Integrations"]
)


@router.get(
    "/",
    response_model=list[WorkspaceIntegrationResponse]
)
def list_integrations(
    workspace_id: int,
    db: Session = Depends(get_db),
    membership: WorkspaceMember = Depends(require_role(WorkspaceRole.owner))
):

    return (
        db.query(WorkspaceIntegration)
        .filter(WorkspaceIntegration.workspace_id == workspace_id)
        .all()
    )


@router.put(
    "/",
    response_model=WorkspaceIntegrationResponse
)
def upsert_integration(
    workspace_id: int,
    payload: WorkspaceIntegrationUpsert,
    db: Session = Depends(get_db),
    membership: WorkspaceMember = Depends(require_role(WorkspaceRole.owner)),
    _demo: None = Depends(require_writable_workspace("change integrations"))
):

    existing = (
        db.query(WorkspaceIntegration)
        .filter(
            WorkspaceIntegration.workspace_id == workspace_id,
            WorkspaceIntegration.provider == payload.provider
        )
        .first()
    )

    if existing:
        existing.config = payload.config
        existing.enabled = payload.enabled
    else:
        existing = WorkspaceIntegration(
            workspace_id=workspace_id,
            provider=payload.provider,
            config=payload.config,
            enabled=payload.enabled,
        )
        db.add(existing)

    db.commit()
    db.refresh(existing)

    return existing


@router.delete("/{provider}")
def delete_integration(
    workspace_id: int,
    provider: IntegrationProvider,
    db: Session = Depends(get_db),
    membership: WorkspaceMember = Depends(require_role(WorkspaceRole.owner)),
    _demo: None = Depends(require_writable_workspace("change integrations"))
):

    integration = (
        db.query(WorkspaceIntegration)
        .filter(
            WorkspaceIntegration.workspace_id == workspace_id,
            WorkspaceIntegration.provider == provider
        )
        .first()
    )
    if integration is None:
        raise HTTPException(status_code=404, detail="Integration not configured")

    db.delete(integration)
    db.commit()

    return {"message": "Integration removed"}


@router.post(
    "/{provider}/test-send",
    response_model=TestSendResponse
)
def test_send(
    workspace_id: int,
    provider: IntegrationProvider,
    db: Session = Depends(get_db),
    membership: WorkspaceMember = Depends(require_role(WorkspaceRole.owner)),
    _demo: None = Depends(require_writable_workspace("send test messages"))
):

    integration = (
        db.query(WorkspaceIntegration)
        .filter(
            WorkspaceIntegration.workspace_id == workspace_id,
            WorkspaceIntegration.provider == provider
        )
        .first()
    )
    if integration is None:
        raise HTTPException(status_code=404, detail="Integration not configured")

    connector = get_connector(integration.provider)
    result = connector.send(
        integration.config or {},
        DeliveryPayload(
            title="Test message",
            body_markdown="This is a test message from Competitive Intelligence Monitor.",
        ),
    )

    return TestSendResponse(success=result.success, detail=result.detail)
