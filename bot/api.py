"""Read-only HTTP API for other services (e.g. a finance bot). Machine-facing, English only."""

import hmac
import logging
from datetime import date, datetime, timedelta

from aiohttp import web
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot.clock import user_today
from bot.db.models import Payment, Subscription, User
from bot.services.notifications import as_utc
from bot.services.subscriptions import (
    payments_between,
    subscriptions_by_status,
    upcoming_charges,
)

log = logging.getLogger(__name__)

STATUSES = ("active", "archived")
# A daily plan over decades would be a huge response; nobody plans that far
MAX_UPCOMING_DAYS = 5 * 366

SESSIONMAKER = web.AppKey("sessionmaker", async_sessionmaker)
TOKEN = web.AppKey("token", str)
TELEGRAM_ID = web.AppKey("telegram_id", int)


def _error(status: int, message: str, headers=None) -> web.Response:
    return web.json_response({"error": message}, status=status, headers=headers)


@web.middleware
async def errors_middleware(request: web.Request, handler) -> web.StreamResponse:
    try:
        return await handler(request)
    except web.HTTPException as exc:
        if exc.status < 400:
            raise
        allow = exc.headers.get("Allow")
        return _error(exc.status, exc.reason, headers={"Allow": allow} if allow else None)
    except Exception:
        log.exception("API request %s failed", request.path)
        return _error(500, "internal error")


@web.middleware
async def auth_middleware(request: web.Request, handler) -> web.StreamResponse:
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    expected = request.app[TOKEN]
    if scheme != "Bearer" or not hmac.compare_digest(token.encode(), expected.encode()):
        return _error(401, "missing or invalid token", headers={"WWW-Authenticate": "Bearer"})
    return await handler(request)


def _bad_request(message: str) -> web.HTTPBadRequest:
    return web.HTTPBadRequest(reason=message)


def _date_param(request: web.Request, name: str) -> date:
    raw = request.query.get(name)
    if raw is None:
        raise _bad_request(f"'{name}' is required")
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise _bad_request(f"'{name}' must be a date in YYYY-MM-DD format") from None


def _timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    return as_utc(value).replace(tzinfo=None).isoformat(timespec="seconds") + "Z"


def subscription_json(sub: Subscription) -> dict:
    return {
        "id": sub.id,
        "user_id": sub.user_id,
        "name": sub.name,
        "amount_minor": sub.amount_minor,
        "currency": sub.currency,
        "period_unit": sub.period_unit,
        "period_count": sub.period_count,
        "anchor_date": sub.anchor_date.isoformat(),
        "next_charge_date": sub.next_charge_date.isoformat(),
        "status": sub.status,
        "auto_renew": bool(sub.auto_renew),
        "trial_end": sub.trial_end.isoformat() if sub.trial_end else None,
        "remind_offsets": sub.remind_offsets,
        "category": sub.category,
        "note": sub.note,
        "created_at": _timestamp(sub.created_at),
        "archived_at": _timestamp(sub.archived_at),
    }


def payment_json(p: Payment) -> dict:
    return {
        "id": p.id,
        "subscription_id": p.subscription_id,
        "charge_date": p.charge_date.isoformat(),
        "amount_minor": p.amount_minor,
        "currency": p.currency,
    }


async def _user(session, request: web.Request) -> User:
    user = await session.scalar(select(User).where(User.tg_id == request.app[TELEGRAM_ID]))
    if user is None:
        raise web.HTTPNotFound(reason="user not found: the bot has not been started yet")
    return user


async def health(_request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


async def subscriptions(request: web.Request) -> web.Response:
    status = request.query.get("status", "active")
    if status not in STATUSES:
        raise _bad_request("'status' must be one of: " + ", ".join(STATUSES))
    async with request.app[SESSIONMAKER]() as session:
        user = await _user(session, request)
        subs = await subscriptions_by_status(session, user.id, status)
    return web.json_response([subscription_json(s) for s in subs])


async def upcoming(request: web.Request) -> web.Response:
    until = _date_param(request, "until")
    async with request.app[SESSIONMAKER]() as session:
        user = await _user(session, request)
        today = user_today(user.timezone)
        if until > today + timedelta(days=MAX_UPCOMING_DAYS):
            raise _bad_request(f"'until' must be within {MAX_UPCOMING_DAYS} days from today")
        subs = await subscriptions_by_status(session, user.id, "active")
    items = [
        {
            "subscription_id": sub.id,
            "name": sub.name,
            "charge_date": charge.isoformat(),
            "amount_minor": sub.amount_minor,
            "currency": sub.currency,
            "auto_renew": bool(sub.auto_renew),
        }
        for sub in subs
        for charge in upcoming_charges(sub, today, until)
    ]
    items.sort(key=lambda i: (i["charge_date"], i["subscription_id"]))
    return web.json_response(items)


async def payments(request: web.Request) -> web.Response:
    start = _date_param(request, "from")
    end = _date_param(request, "to")
    if start > end:
        raise _bad_request("'from' must not be later than 'to'")
    async with request.app[SESSIONMAKER]() as session:
        user = await _user(session, request)
        rows = await payments_between(session, user.id, start, end)
    return web.json_response([payment_json(p) for p in rows])


def create_app(sessionmaker: async_sessionmaker, token: str, telegram_id: int) -> web.Application:
    # Errors outermost, so a 401 from auth and 404/405 from routing all become JSON
    app = web.Application(middlewares=[errors_middleware, auth_middleware])
    app[SESSIONMAKER] = sessionmaker
    app[TOKEN] = token
    app[TELEGRAM_ID] = telegram_id
    app.router.add_get("/api/v1/health", health, allow_head=False)
    app.router.add_get("/api/v1/subscriptions", subscriptions, allow_head=False)
    app.router.add_get("/api/v1/subscriptions/upcoming", upcoming, allow_head=False)
    app.router.add_get("/api/v1/payments", payments, allow_head=False)
    return app
