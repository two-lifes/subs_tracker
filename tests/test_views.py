"""Rendering of cards, the list and payment history."""

from datetime import date, datetime

from bot.db.models import Payment, Subscription, User
from bot.services.money import NBSP
from bot.views import list_text, payments_text, sub_card

TODAY = date(2026, 10, 16)
USER = User(tg_id=1, timezone="Europe/Moscow", default_offsets=[3, 0])


def make_sub(name="Claude Code", amount=2224, currency="USD", next_charge=date(2026, 11, 16),
             **kw) -> Subscription:
    kw.setdefault("status", "active")
    kw.setdefault("auto_renew", True)
    return Subscription(id=kw.pop("id", 1), name=name, amount_minor=amount, currency=currency,
                        period_unit=kw.pop("unit", "month"), period_count=1,
                        anchor_date=date(2026, 10, 16), next_charge_date=next_charge, **kw)


def nb(text: str) -> str:
    return text.replace("_", NBSP)


def test_card_with_autopay():
    assert sub_card(make_sub(), USER, TODAY) == nb(
        "💳 <b>Claude Code</b>\n"
        "<b>22.24_$</b> в месяц · автоплатёж\n"
        "📅 Списание: <b>16.11.2026</b>, через 31 день\n"
        "🔔 Напоминания: за 3 дн., в день списания"
    )


def test_card_manual_overdue_with_extras_escaped():
    sub = make_sub("Gym", 150000, "RUB", date(2026, 10, 10), auto_renew=False,
                   category="Спорт & <здоровье>", note="карта *1234", remind_offsets=[])
    assert sub_card(sub, USER, TODAY) == nb(
        "⚠️ <b>Gym</b>\n"
        "<b>1_500_₽</b> в месяц · оплата вручную\n"
        "⚠️ Ждёт оплаты с <b>10.10.2026</b>, 6 дней назад\n"
        "🔔 Напоминания: выключены\n"
        "<blockquote>🏷 Спорт &amp; &lt;здоровье&gt;\n📝 карта *1234</blockquote>"
    )


def test_card_manual_not_due_and_archived():
    manual = make_sub(auto_renew=False)
    assert sub_card(manual, USER, TODAY).splitlines()[0] == "🧾 <b>Claude Code</b>"
    assert "📅 Оплатить до: <b>16.11.2026</b>" in sub_card(manual, USER, TODAY)

    archived = make_sub(status="archived", archived_at=datetime(2026, 10, 1, 21, 0))
    lines = sub_card(archived, USER, TODAY).splitlines()
    assert lines[0] == "🗄 <b>Claude Code</b>"
    # 21:00 UTC is already the next day in Moscow
    assert lines[2] == "В архиве с 02.10.2026"
    assert not any("Напоминания" in line for line in lines)


def test_list_groups_soon_and_later():
    subs = [
        make_sub("Gym", 150000, "RUB", date(2026, 10, 10), id=1, auto_renew=False),
        make_sub("Claude Code", 2224, "USD", date(2026, 10, 23), id=2),  # exactly 7 days
        make_sub("Domain", 1200, "EUR", date(2027, 3, 1), id=3, unit="year"),
    ]
    assert list_text(subs, TODAY) == nb(
        "📋 <b>Подписки</b> · 3\n"
        "\n<b>Скоро, 7 дней</b>\n"
        "⚠️ <b>Gym</b> — 1_500_₽ в месяц\n      ждёт оплаты с 10.10\n"
        "💳 <b>Claude Code</b> — 22.24_$ в месяц\n      23.10, через 7 дней\n"
        "\n<b>Позже</b>\n"
        "💳 <b>Domain</b> — 12_€ в год\n      01.03, через 136 дней\n"
        "\n<b>В среднем в месяц:</b> 1_€ + 1_500_₽ + 22.24_$"
    )


def test_list_without_soon_group():
    text = list_text([make_sub()], TODAY)
    assert "Скоро" not in text and "<b>Позже</b>" in text


def test_payment_history_is_a_right_aligned_column():
    payments = [
        Payment(charge_date=date(2026, 10, 16), amount_minor=2224, currency="USD"),
        Payment(charge_date=date(2026, 9, 16), amount_minor=999, currency="USD"),
        Payment(charge_date=date(2026, 8, 16), amount_minor=10000, currency="USD"),
    ]
    assert payments_text(make_sub(name="A<B"), payments) == nb(
        "📜 <b>A&lt;B</b> — история платежей\n<pre>"
        "16.10.2026  22.24_$\n"
        "16.09.2026   9.99_$\n"
        "16.08.2026    100_$"
        "</pre>"
    )
