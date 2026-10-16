from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from bot.db.models import Deployment
from bot.services.deployments import list_deployments, record_startup

NOW = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)


async def rows(session) -> list[tuple[str, str]]:
    result = await session.scalars(select(Deployment).order_by(Deployment.id))
    return [(d.version, d.status) for d in result]


async def test_restart_of_same_version_is_recorded_once(session):
    await record_startup(session, "aaa", "First", None, NOW)
    await record_startup(session, "aaa", "First", None, NOW + timedelta(hours=1))
    await record_startup(session, "bbb", "Second", None, NOW + timedelta(hours=2))
    assert await rows(session) == [("aaa", "ok"), ("bbb", "ok")]


async def test_local_run_records_nothing(session):
    await record_startup(session, None, None, None, NOW)
    assert await rows(session) == []


async def test_failed_deploy_and_rollback(session):
    await record_startup(session, "aaa", "First", None, NOW)
    # bbb did not start; deploy.sh restarted aaa with FAILED_VERSION=bbb
    await record_startup(session, "aaa", "First", "bbb", NOW + timedelta(hours=1))
    # The container restarts with the same env (e.g. a server reboot)
    await record_startup(session, "aaa", "First", "bbb", NOW + timedelta(hours=2))
    assert await rows(session) == [("aaa", "ok"), ("bbb", "failed")]


async def test_long_title_is_truncated_and_history_is_newest_first(session):
    await record_startup(session, "aaa", "x" * 500, None, NOW)
    await record_startup(session, "bbb", "", None, NOW)
    first, second = await list_deployments(session)
    assert (first.version, first.title) == ("bbb", None)
    assert second.title == "x" * 200
