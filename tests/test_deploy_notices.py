"""The owner hears about new bot versions and can see their history in settings."""

from datetime import UTC, datetime, timedelta

from aiogram.exceptions import TelegramNetworkError
from aiogram.methods import GetMe

from bot.keyboards.menu import BTN_SETTINGS
from bot.services.deployments import record_startup
from tests.harness import onboard
from tests.tg_fakes import OWNER_ID

NOW = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)
SHA = "afe3920a243eacb59517ab1544090d9886e53ffa"


async def start(h, version=SHA, title="Add <deploy> notices", failed=None) -> None:
    async with h.sessionmaker() as session, session.begin():
        await record_startup(session, version, title, failed, NOW)


async def test_new_version_is_announced_once(h):
    await onboard(h)
    await start(h)
    h.net.requests.clear()
    await h.tick(NOW)
    await h.tick(NOW + timedelta(minutes=1))

    [msg] = h.net.sent()
    assert msg.chat_id == OWNER_ID
    assert msg.text == "🔄 Бот обновлён: <code>afe3920</code> — Add &lt;deploy&gt; notices"


async def test_failed_version_is_announced(h):
    await onboard(h)
    await start(h)
    await h.tick(NOW)
    h.net.requests.clear()
    await start(h, failed="bad0000ffff")
    await h.tick(NOW)
    [msg] = h.net.sent()
    assert msg.text.startswith("❌ Обновление <code>bad0000</code> не запустилось")


async def test_notice_waits_for_network(h):
    await onboard(h)
    await start(h)
    h.net.requests.clear()
    h.net.fail_next = TelegramNetworkError(method=GetMe(), message="offline")
    await h.tick(NOW)
    assert h.net.sent() == []
    await h.tick(NOW + timedelta(minutes=1))
    assert len(h.net.sent()) == 1


async def test_notice_waits_for_first_start(h):
    # A fresh server DB: the bot was deployed before the owner pressed /start
    await start(h)
    await h.tick(NOW)
    assert h.net.sent() == []
    await onboard(h)
    h.net.requests.clear()
    await h.tick(NOW + timedelta(minutes=1))
    assert len(h.net.sent()) == 1


async def test_settings_show_update_history(h):
    await onboard(h)
    await h.tap("settings:updates")
    assert "Истории пока нет" in h.net.last_text()

    await start(h)
    await start(h, failed="bad0000ffff")
    await h.text(BTN_SETTINGS)
    await h.tap("settings:updates")
    text = h.net.last_text()
    assert "Текущая версия: <code>afe3920</code>" in text
    # Newest first, time in the owner's zone (Moscow, UTC+3)
    lines = text.splitlines()[3:]
    assert lines == [
        "16.10.2026 15:00 <code>bad0000</code> ❌ не запустилось, откат",
        "16.10.2026 15:00 <code>afe3920</code> ✅ Add &lt;deploy&gt; notices",
    ]
    await h.tap("settings:back")
    assert h.net.last_text().startswith("⚙️ <b>Настройки</b>")
