"""End-to-end dialog tests: real dispatcher, real handlers, fake Telegram network."""

from bot.app import ERROR_TEXT
from bot.clock import user_today
from bot.handlers import subs as subs_handlers
from bot.keyboards.menu import BTN_ADD, BTN_LIST
from bot.keyboards.subs import EditCb, PeriodCb, SubCb
from bot.services.billing import PeriodUnit, charge_date
from tests.harness import TZ, onboard
from tests.tg_fakes import STRANGER_ID, sticker_update

MONTH = PeriodCb(unit=PeriodUnit.MONTH).pack()


async def test_add_amount_then_currency(h):
    await onboard(h)
    await h.text(BTN_ADD)
    await h.text("Claude Code")
    await h.text("22.34")
    assert "В какой валюте" in h.net.last_text()
    await h.tap("acur:EUR")
    await h.tap(MONTH)
    await h.tap("pay:auto")
    await h.tap("paid:0")
    await h.tap("add:save")

    [sub] = await h.subs()
    today = user_today(TZ)
    assert (sub.name, sub.amount_minor, sub.currency) == ("Claude Code", 2234, "EUR")
    assert sub.next_charge_date == charge_date(today, PeriodUnit.MONTH, 1, 1)
    assert any("Добавлено" in t for t in h.net.texts())


async def test_add_currency_then_amount(h):
    await onboard(h)
    await h.text(BTN_ADD)
    await h.text("Spotify")
    await h.tap("acur:RUB")
    assert "например <code>2000</code>" in h.net.last_text()
    await h.text("299")
    await h.tap(MONTH)
    await h.tap("pay:auto")
    await h.text("01.10")
    await h.tap("add:save")

    [sub] = await h.subs()
    assert (sub.amount_minor, sub.currency) == (29900, "RUB")


async def test_currency_precision_is_rechecked(h):
    await onboard(h)
    await h.text(BTN_ADD)
    await h.text("Some JP service")
    await h.text("9.5")
    await h.tap("acur:JPY")
    assert "целым числом" in h.net.last_text()
    await h.text("990")
    await h.tap(MONTH)
    await h.tap("pay:auto")
    await h.tap("paid:0")
    await h.tap("add:save")
    [sub] = await h.subs()
    assert (sub.amount_minor, sub.currency) == (990, "JPY")


async def test_menu_button_interrupts_dialog(h):
    await onboard(h)
    await h.text(BTN_ADD)
    await h.text(BTN_LIST)  # must not become the subscription name
    assert await h.subs() == []
    assert "Подписок пока нет" in h.net.last_text()


async def test_settings_prompts_show_current_values(h):
    await onboard(h)
    await h.tap("settings:offsets")
    assert "Сейчас: <code>3, 0</code> — за 3 дн., в день списания" in h.net.last_text()
    await h.text("7, 1")
    await h.tap("settings:offsets")
    assert "Сейчас: <code>7, 1</code>" in h.net.last_text()

    await h.tap("settings:time")
    assert "Сейчас: <code>10:00</code>" in h.net.last_text()
    await h.text("9:30")
    await h.tap("settings:time")
    assert "Сейчас: <code>09:30</code>" in h.net.last_text()


async def test_edit_prompts_show_current_values(h):
    await onboard(h)
    await h.text(BTN_ADD)
    await h.text("Claude <Code>")
    await h.tap("acur:USD")
    await h.text("22.34")
    await h.tap(MONTH)
    await h.tap("pay:auto")
    await h.tap("paid:0")
    await h.tap("add:save")
    [sub] = await h.subs()
    today = user_today(TZ)

    expected = {
        "name": "Сейчас: <code>Claude &lt;Code&gt;</code>.",
        "amount": "Сейчас: 22.34 $.",
        "currency": "Сейчас: USD.",
        "period": "Сейчас: в месяц.",
        "paid_on": f"Сейчас: последняя оплата {today:%d.%m.%Y}.",
        "offsets": "Сейчас: как по умолчанию — за 3 дн., в день списания.",
        "category": "Сейчас: не задано.",
        "note": "Сейчас: не задано.",
    }
    for field, text in expected.items():
        await h.tap(EditCb(field=field, id=sub.id).pack())
        assert h.net.last_text().startswith(text), field
        await h.text("/cancel")

    await h.tap(EditCb(field="offsets", id=sub.id).pack())
    await h.text("7")
    await h.tap(EditCb(field="offsets", id=sub.id).pack())
    assert h.net.last_text().startswith("Сейчас: <code>7</code> — за 7 дн.")


async def add_sub(h, name: str = "Claude Code") -> None:
    await h.text(BTN_ADD)
    await h.text(name)
    await h.tap("acur:USD")
    await h.text("22.34")
    await h.tap(MONTH)
    await h.tap("pay:auto")
    await h.tap("paid:0")
    await h.tap("add:save")


async def test_text_where_button_expected_gets_a_hint(h):
    await onboard(h)
    await h.text(BTN_ADD)
    await h.text("Claude Code")
    await h.tap("acur:USD")
    await h.text("22.34")
    await h.text("каждый месяц")  # the period is chosen by buttons
    assert "Нажмите кнопку в сообщении выше" in h.net.last_text()


async def test_unknown_message_outside_dialog_shows_menu(h):
    await onboard(h)
    await h.text("2000")  # e.g. an answer to a dialog forgotten after a restart
    assert "Выберите действие в меню" in h.net.last_text()
    await h.feed(sticker_update())
    assert "Выберите действие в меню" in h.net.last_text()


async def test_handler_crash_is_reported_and_rolled_back(h, monkeypatch):
    await onboard(h)
    await add_sub(h)
    [sub] = await h.subs()

    async def broken_show(*args, **kwargs):
        raise RuntimeError("boom")

    # The archive change happens before the crash and must not be committed
    monkeypatch.setattr(subs_handlers, "_show_card", broken_show)
    await h.tap(SubCb(action="archive", id=sub.id).pack())
    assert h.net.callback_answers()[-1] == ERROR_TEXT
    [sub] = await h.subs()
    assert sub.status == "active"


async def test_buttons_work_on_inaccessible_messages(h):
    await onboard(h)
    await add_sub(h)
    [sub] = await h.subs()
    h.net.requests.clear()
    await h.tap(SubCb(action="open", id=sub.id).pack(), inaccessible=True)
    await h.tap(SubCb(action="archive", id=sub.id).pack(), inaccessible=True)

    assert [m.text.split("\n")[0] for m in h.net.sent()] == [
        "💳 <b>Claude Code</b>",
        "🗄 <b>Claude Code</b>",  # the card after archiving
    ]
    [sub] = await h.subs()
    assert sub.status == "archived"


async def test_names_with_html_characters_are_escaped(h):
    await onboard(h)
    await add_sub(h, "A&B <Pro>")
    [sub] = await h.subs()
    await h.tap(SubCb(action="delete", id=sub.id).pack())
    assert "«A&amp;B &lt;Pro&gt;»" in h.net.last_text()
    await h.tap(SubCb(action="edit", id=sub.id).pack())
    assert "«A&amp;B &lt;Pro&gt;»" in h.net.last_text()


async def test_stranger_is_ignored(h):
    await h.text("/start", user_id=STRANGER_ID)
    assert h.net.requests == []


async def test_edit_price_and_archive(h):
    await onboard(h)
    await h.text(BTN_ADD)
    await h.text("Claude Code")
    await h.tap("acur:USD")
    await h.text("22.34")
    await h.tap(MONTH)
    await h.tap("pay:auto")
    await h.tap("paid:0")
    await h.tap("add:save")
    [sub] = await h.subs()

    await h.tap(EditCb(field="amount", id=sub.id).pack())
    await h.text("25")
    await h.tap(SubCb(action="archive", id=sub.id).pack())

    [sub] = await h.subs()
    assert sub.amount_minor == 2500
    assert sub.status == "archived"


async def test_stale_button_is_answered(h):
    await onboard(h)
    h.net.requests.clear()
    await h.tap("acur:USD")  # no dialog is waiting for a currency
    assert len(h.net.requests) == 1
