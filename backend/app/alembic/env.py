import sys
from pathlib import Path
from logging.config import fileConfig

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

# backend/ (parent of app/) must be on sys.path so `import app...` resolves
# regardless of the cwd alembic is invoked from.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.core.config import settings  # noqa: E402
from app.base import Base  # noqa: E402
from app.models.user import User  # noqa: E402
from app.models.competitor import Competitor  # noqa: E402
from app.models.change_log import ChangeLog  # noqa: E402
from app.models.workspace import Workspace  # noqa: E402
from app.models.workspace_member import WorkspaceMember  # noqa: E402
from app.models.surface import Surface  # noqa: E402
from app.models.snapshot import Snapshot  # noqa: E402
from app.models.llm_usage import TokenUsageLog  # noqa: E402
from app.models.workspace_budget import WorkspaceBudget  # noqa: E402
from app.models.briefing import Briefing  # noqa: E402
from app.models.briefing_job import BriefingJob  # noqa: E402
from app.models.approval_item import ApprovalItem  # noqa: E402
from app.models.audit_log import AuditLog  # noqa: E402
from app.models.battlecard import Battlecard  # noqa: E402
from app.models.battlecard_update import BattlecardUpdate  # noqa: E402
from app.models.response_library import ResponseLibraryItem  # noqa: E402
from app.models.workspace_integration import WorkspaceIntegration  # noqa: E402
from app.models.company_profile import CompanyProfile  # noqa: E402
from app.models.check_run import CheckRun  # noqa: E402
from app.models.change_embedding import ChangeEmbedding  # noqa: E402
from app.models.traffic_snapshot import TrafficSnapshot  # noqa: E402
from app.models.competitor_site_summary import CompetitorSiteSummary  # noqa: E402

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config
config.set_main_option(
    "sqlalchemy.url",
    settings.database_url.replace("%", "%%")
)

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
