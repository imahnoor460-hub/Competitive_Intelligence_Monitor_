"""Which of a competitor's pages are actually watched.

Discovery is deliberately generous — a storefront's sitemap offers hundreds
of URLs and one pass keeps up to 40 of them. Watching is not: every watched
page is a daily scheduled fetch and a slot in every "Run check now" sweep, and
this runs on 0.2 shared CPU. A workspace that had accumulated 282 active
surfaces turned one click into 282 sequential checks, which is what made a
sweep look permanently stuck.

So discovery still finds everything, and this module decides which of the
findings are watched. The rest are kept as inactive rows — nothing schedules
or sweeps them, and a user can still turn one on by hand — rather than being
thrown away, because "we found this page and chose not to watch it" is
information worth keeping.

The ranking is deliberately boring and total: homepage, then the typed pages
a competitor analyst actually asks about, then oldest-discovered first. Being
deterministic matters more than being clever — the same set has to come out
in the scheduler, in the sweep, and in the cleanup migration, or the three
disagree about what is being watched.
"""

from urllib.parse import urlsplit

from app.core.config import settings
from app.models.surface import Surface, SurfaceType

__all__ = ["surface_rank", "partition_by_cap", "cap_for_competitor"]

# pricing first: it is the one page whose change is always material. `other`
# last because sitemap discovery types most category pages that way, so it is
# the bucket that would otherwise crowd out everything specific.
_TYPE_PRIORITY = {
    SurfaceType.pricing: 0,
    SurfaceType.product: 1,
    SurfaceType.changelog: 2,
    SurfaceType.blog: 3,
    SurfaceType.jobs: 4,
    SurfaceType.other: 5,
}


def _is_homepage(surface: Surface) -> bool:
    return urlsplit(surface.url or "").path.strip("/") == ""


def surface_rank(surface: Surface) -> tuple[int, int, int]:
    """Sort key — lower is kept first.

    The homepage always wins: it is what `create_competitor` seeds, what
    discovery re-seeds from, and the one page whose absence would leave a
    competitor with nothing watched at all.
    """

    return (
        0 if _is_homepage(surface) else 1,
        _TYPE_PRIORITY.get(surface.surface_type, len(_TYPE_PRIORITY)),
        surface.id or 0,
    )


def cap_for_competitor() -> int:
    return settings.max_active_surfaces_per_competitor


def partition_by_cap(
    surfaces: list[Surface], limit: int | None = None
) -> tuple[list[Surface], list[Surface]]:
    """Split one competitor's surfaces into (watched, not watched).

    Returns lists rather than mutating, so the same function serves the
    scheduler, the sweep and the cleanup migration without any of them
    inheriting the others' side effects.
    """

    if limit is None:
        limit = cap_for_competitor()

    ordered = sorted(surfaces, key=surface_rank)
    return ordered[:limit], ordered[limit:]
