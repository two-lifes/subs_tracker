"""User-facing text rendering (Russian, HTML parse mode)."""

from collections import defaultdict
from datetime import date
from decimal import Decimal
from html import escape
from zoneinfo import ZoneInfo

from bot.db.models import Deployment, Payment, Subscription, User
from bot.services.billing import PeriodUnit, monthly_amount
from bot.services.dates import relative_days
from bot.services.deployments import STATUS_OK
from bot.services.money import format_amount
from bot.services.notifications import as_utc, is_overdue

_SINGLE = {
    PeriodUnit.DAY: "в день",
    PeriodUnit.WEEK: "в неделю",
    PeriodUnit.MONTH: "в месяц",
    PeriodUnit.YEAR: "в год",
}
_SHORT = {
    PeriodUnit.DAY: "дн.",
    PeriodUnit.WEEK: "нед.",
    PeriodUnit.MONTH: "мес.",
    PeriodUnit.YEAR: "г.",
}


def format_period(unit: str, count: int) -> str:
    unit = PeriodUnit(unit)
    return _SINGLE[unit] if count == 1 else f"каждые {count} {_SHORT[unit]}"


def format_offsets(offsets: list[int]) -> str:
    parts = ["в день списания" if d == 0 else f"за {d} дн." for d in sorted(offsets, reverse=True)]
    return ", ".join(parts) if parts else "выключены"


def price_line(sub: Subscription) -> str:
    amount = format_amount(sub.amount_minor, sub.currency)
    return f"{amount} {format_period(sub.period_unit, sub.period_count)}"


def sub_button_text(sub: Subscription, today: date) -> str:
    mark = "⚠️ " if is_overdue(sub, today) else ""
    return f"{mark}{sub.name} · {sub.next_charge_date:%d.%m}"


def sub_icon(sub: Subscription, today: date) -> str:
    if sub.status == "archived":
        return "🗄"
    if is_overdue(sub, today):
        return "⚠️"
    return "💳" if sub.auto_renew else "🧾"


def _due_line(sub: Subscription, today: date) -> str:
    charge = sub.next_charge_date
    when = f"<b>{charge:%d.%m.%Y}</b>, {relative_days(charge, today)}"
    if sub.auto_renew:
        return f"📅 Списание: {when}"
    if is_overdue(sub, today):
        return f"⚠️ Ждёт оплаты с {when}"
    return f"📅 Оплатить до: {when}"


def sub_card(sub: Subscription, user: User, today: date) -> str:
    paymode = "автоплатёж" if sub.auto_renew else "оплата вручную"
    lines = [
        f"{sub_icon(sub, today)} <b>{escape(sub.name)}</b>",
        f"<b>{format_amount(sub.amount_minor, sub.currency)}</b> "
        f"{format_period(sub.period_unit, sub.period_count)} · {paymode}",
    ]
    if sub.status == "archived":
        since = ""
        if sub.archived_at:
            local = as_utc(sub.archived_at).astimezone(ZoneInfo(user.timezone or "UTC"))
            since = f" с {local:%d.%m.%Y}"
        lines.append(f"В архиве{since}")
    else:
        lines.append(_due_line(sub, today))
        offsets = sub.remind_offsets if sub.remind_offsets is not None else user.default_offsets
        lines.append(f"🔔 Напоминания: {format_offsets(offsets)}")
    extra = []
    if sub.category:
        extra.append(f"🏷 {escape(sub.category)}")
    if sub.note:
        extra.append(f"📝 {escape(sub.note)}")
    if extra:
        lines.append("<blockquote>" + "\n".join(extra) + "</blockquote>")
    return "\n".join(lines)


def current_value(field: str, sub: Subscription, user: User, last_paid: date | None) -> str:
    """Current value of an editable field, shown before asking for a new one."""
    match field:
        case "name":
            return f"<code>{escape(sub.name)}</code>"
        case "amount":
            return format_amount(sub.amount_minor, sub.currency)
        case "currency":
            return sub.currency
        case "period":
            return format_period(sub.period_unit, sub.period_count)
        case "paid_on":
            paid = last_paid or sub.anchor_date
            return f"последняя оплата {paid:%d.%m.%Y}"
        case "offsets":
            if sub.remind_offsets is None:
                return f"как по умолчанию — {format_offsets(user.default_offsets)}"
            raw = ", ".join(str(d) for d in sub.remind_offsets) or "-"
            return f"<code>{raw}</code> — {format_offsets(sub.remind_offsets)}"
        case "category" | "note":
            value = getattr(sub, field)
            return f"<code>{escape(value)}</code>" if value else "не задано"
    return ""


def monthly_totals(subs: list[Subscription]) -> dict[str, Decimal]:
    totals: dict[str, Decimal] = defaultdict(Decimal)
    for sub in subs:
        totals[sub.currency] += monthly_amount(
            sub.amount_minor, PeriodUnit(sub.period_unit), sub.period_count
        )
    return dict(totals)


# Charges this close are grouped under "Скоро" in the list
SOON_DAYS = 7


def _list_line(sub: Subscription, today: date) -> str:
    charge = sub.next_charge_date
    if is_overdue(sub, today):
        when = f"ждёт оплаты с {charge:%d.%m}"
    else:
        when = f"{charge:%d.%m}, {relative_days(charge, today)}"
    return f"{sub_icon(sub, today)} <b>{escape(sub.name)}</b> — {price_line(sub)}\n      {when}"


def list_text(subs: list[Subscription], today: date) -> str:
    """``subs`` go in charge date order."""
    if not subs:
        return "Подписок пока нет. Нажмите «➕ Добавить»."
    soon = [s for s in subs if (s.next_charge_date - today).days <= SOON_DAYS]
    later = [s for s in subs if s not in soon]
    lines = [f"📋 <b>Подписки</b> · {len(subs)}"]
    for title, group in ((f"Скоро, {SOON_DAYS} дней", soon), ("Позже", later)):
        if group:
            lines.append(f"\n<b>{title}</b>")
            lines += [_list_line(s, today) for s in group]
    totals = monthly_totals(subs)
    lines.append(
        "\n<b>В среднем в месяц:</b> "
        + " + ".join(format_amount(v, c) for c, v in sorted(totals.items()))
    )
    return "\n".join(lines)


def notification_text(kind: str, charge: date, sub: Subscription, today: date) -> str:
    name = f"<b>{escape(sub.name)}</b>"
    amount = f"<b>{format_amount(sub.amount_minor, sub.currency)}</b>"
    if kind == "charged":
        return (
            f"💳 Сегодня списание за {name}: {amount}.\n"
            f"Следующее — <b>{sub.next_charge_date:%d.%m.%Y}</b>."
        )
    if kind == "due":
        if charge < today:
            return (
                f"⚠️ Напоминаю: {name} ждёт оплаты — срок был {charge:%d.%m.%Y} "
                f"({relative_days(charge, today)}), {amount}.\nОплатили?"
            )
        return f"📅 Сегодня срок оплаты {name}: {amount}.\nОплатили?"
    when = f"⏰ {relative_days(charge, today).capitalize()} ({charge:%d.%m})"
    if sub.auto_renew:
        return f"{when} спишется {amount} за {name}.\nЕсли не нужна — самое время отменить."
    return f"{when} срок оплаты {name}: {amount}."


def payments_text(sub: Subscription, payments: list[Payment]) -> str:
    if not payments:
        return f"📜 <b>{escape(sub.name)}</b>: платежей пока нет."
    amounts = [format_amount(p.amount_minor, p.currency) for p in payments]
    width = max(len(a) for a in amounts)
    # Monospace block, so amounts line up in a right-aligned column
    rows = [
        f"{p.charge_date:%d.%m.%Y}  {a:>{width}}"
        for p, a in zip(payments, amounts, strict=True)
    ]
    title = f"📜 <b>{escape(sub.name)}</b> — история платежей"
    return f"{title}\n<pre>" + "\n".join(rows) + "</pre>"


def _short(version: str) -> str:
    return f"<code>{escape(version[:7])}</code>"


def deployment_text(d: Deployment) -> str:
    if d.status == STATUS_OK:
        title = f" — {escape(d.title)}" if d.title else ""
        return f"🔄 Бот обновлён: {_short(d.version)}{title}"
    return f"❌ Обновление {_short(d.version)} не запустилось, работает прежняя версия."


def deployments_text(deployments: list[Deployment], tz: str) -> str:
    """``deployments`` go newest first."""
    if not deployments:
        return "<b>Обновления</b>\nИстории пока нет: она ведётся только на сервере."
    lines = ["<b>Обновления</b>"]
    current = next((d for d in deployments if d.status == STATUS_OK), None)
    if current is not None:
        lines.append(f"Текущая версия: {_short(current.version)}")
    lines.append("")
    for d in deployments:
        when = as_utc(d.created_at).astimezone(ZoneInfo(tz))
        if d.status == STATUS_OK:
            status = f"✅ {escape(d.title)}" if d.title else "✅ установлено"
        else:
            status = "❌ не запустилось, откат"
        lines.append(f"{when:%d.%m.%Y %H:%M} {_short(d.version)} {status}")
    return "\n".join(lines)
