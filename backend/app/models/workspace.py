from datetime import datetime

from sqlalchemy import Boolean, Column, Integer, String, DateTime
from app.base import Base


class Workspace(Base):
    __tablename__ = "workspaces"

    id = Column(Integer, primary_key=True, index=True)

    name = Column(String, nullable=False)
    slug = Column(String, unique=True, nullable=False, index=True)

    # The public demo workspace: readable by anyone who clicks "Try the demo",
    # writable by nobody. Nothing in the API can set this — it is written by
    # scripts/provision_demo.py, so a visitor cannot mark their own workspace
    # as a demo (harmless) or clear the flag on this one (not harmless).
    #
    # Keyed to the workspace rather than to the demo user's role on purpose:
    # that account stays `owner` so it can be seeded and repaired, while the
    # restriction follows the data everyone shares. See
    # dependencies.require_writable_workspace.
    is_demo = Column(Boolean, nullable=False, default=False, server_default="false")

    created_at = Column(
        DateTime,
        default=datetime.utcnow
    )
