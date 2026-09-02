from datetime import datetime

from sqlalchemy.orm import Session

from app.models.workspace_integration import WorkspaceIntegration, IntegrationProvider
from app.models.approval_item import ApprovalItem, ApprovalItemType
from app.models.briefing import Briefing, BriefingStatus, BriefingDigestType
from app.models.audit_log import AuditLog
from app.models.workspace import Workspace
from app.services.delivery.base import DeliveryPayload
from app.services.delivery.slack_connector import SlackConnector
from app.services.delivery.email_connector import EmailConnector
from app.services.delivery.crm_connector import CRMConnector

__all__ = ["deliver", "deliver_digest", "get_connector"]

_CONNECTORS = {
    IntegrationProvider.slack: SlackConnector(),
    IntegrationProvider.email: EmailConnector(),
    IntegrationProvider.crm: CRMConnector(),
}


def get_connector(provider: IntegrationProvider):
    return _CONNECTORS[provider]


def deliver(db: Session, approval_item: ApprovalItem) -> None:
    """Called only from approval_service.decide() on approval. An
    'urgent' briefing delivers immediately; 'daily'/'weekly' ones wait for
    the next scheduled digest (see deliver_digest / scheduler.py) instead —
    that's the whole point of a digest cadence. Battlecard updates apply
    their content directly on approval (battlecard_service) and have no
    separate delivery step.
    """

    if approval_item.item_type != ApprovalItemType.briefing:
        return

    briefing = db.query(Briefing).filter(Briefing.id == approval_item.item_id).first()
    if briefing is None or briefing.digest_type != BriefingDigestType.urgent:
        return

    _deliver_briefings(db, approval_item.workspace_id, [briefing])
    db.commit()


def deliver_digest(db: Session, workspace_id: int, digest_type: BriefingDigestType) -> None:
    briefings = (
        db.query(Briefing)
        .filter(
            Briefing.workspace_id == workspace_id,
            Briefing.status == BriefingStatus.approved,
            Briefing.digest_type == digest_type,
        )
        .all()
    )
    if not briefings:
        return

    _deliver_briefings(db, workspace_id, briefings)
    db.commit()


def _deliver_briefings(db: Session, workspace_id: int, briefings: list[Briefing]) -> bool:
    # The demo never sends anything outward. Enforced here rather than only at
    # the approval endpoint because this is the one function both delivery
    # paths funnel through, and the digest path has no request behind it — the
    # scheduler calls deliver_digest for every workspace holding approved
    # briefings, so a router-level guard would not see it.
    #
    # The briefing stays `approved` and fully readable in the UI; only the
    # send is suppressed. The audit row records that, so it is visible what
    # would have gone out.
    workspace = db.query(Workspace).filter(Workspace.id == workspace_id).first()
    if workspace is not None and workspace.is_demo:
        db.add(AuditLog(
            workspace_id=workspace_id,
            actor_user_id=None,
            action="delivery.suppressed.demo",
            entity_type="briefing",
            entity_id=briefings[0].id if len(briefings) == 1 else None,
            extra_data={"briefing_ids": [b.id for b in briefings]},
        ))
        return False

    integrations = (
        db.query(WorkspaceIntegration)
        .filter(
            WorkspaceIntegration.workspace_id == workspace_id,
            WorkspaceIntegration.enabled.is_(True),
        )
        .all()
    )
    if not integrations:
        # Nothing configured — briefings stay 'approved', not 'delivered',
        # so it's visible in the UI that delivery is still pending setup.
        return False

    if len(briefings) == 1:
        payload = DeliveryPayload(
            title=briefings[0].title, body_markdown=briefings[0].body_markdown
        )
    else:
        payload = DeliveryPayload(
            title=f"Digest: {len(briefings)} briefings",
            body_markdown="\n\n---\n\n".join(
                f"### {b.title}\n{b.body_markdown}" for b in briefings
            ),
        )

    any_success = False
    for integration in integrations:
        connector = _CONNECTORS.get(integration.provider)
        if connector is None:
            continue

        result = connector.send(integration.config or {}, payload)

        db.add(AuditLog(
            workspace_id=workspace_id,
            actor_user_id=None,
            action=f"delivery.{integration.provider.value}.{'success' if result.success else 'failure'}",
            entity_type="briefing",
            entity_id=briefings[0].id if len(briefings) == 1 else None,
            extra_data={"detail": result.detail, "briefing_ids": [b.id for b in briefings]}
            if result.detail else {"briefing_ids": [b.id for b in briefings]},
        ))

        if result.success:
            any_success = True

    if any_success:
        now = datetime.utcnow()
        for briefing in briefings:
            briefing.status = BriefingStatus.delivered
            briefing.delivered_at = now

    return any_success
