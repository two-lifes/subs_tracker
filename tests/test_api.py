"""Read-only HTTP API: auth, filters, formats and upcoming charges consistent with the bot."""

import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError

from bot import clock
from bot.api import create_app
from bot.config import Config
from bot.db.models import Base, Payment, Subscription, User
from bot.db.session import create_engine, create_sessionmaker
from tests.harness import onboard
from tests.tg_fakes import OWNER_ID

TOKEN = "s3cret-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
# 12:00 in Moscow
NOW = datetime(2026, 10, 16, 9, 0, tzinfo=UTC)


@pytest.fixture
def now():
    current = {"now": NOW}
    clock.set_clock(lambda: current["now"])
    yield current
    clock.set_clock(lambda: datetime.now(UTC))


def readonly_engine(db_path: Path):
    config = Config(bot_token="x", allowed_user_id=OWNER_ID, db_path=db_path)
    return create_engine(config.db_readonly_url)


class Api:
    def __init__(self, db, client):
        self.db, self.client = db, client

    async def get(self, path: str, headers=AUTH, **params):
        resp = await self.client.get(path, headers=headers, params=params)
        return resp.status, await resp.json()

    async def add(self, *rows):
        async with self.db() as session, session.begin():
            session.add_all(rows)
            await session.flush()
            return [r.id for r in rows]


@pytest.fixture
async def api(tmp_path, now):
    db_path = tmp_path / "api test.db"  # a space checks URI escaping
    rw = create_engine(f"sqlite+aiosqlite:///{db_path.as_posix()}")
    async with rw.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    db = create_sessionmaker(rw)
    async with db() as session, session.begin():
        session.add_all(
            [
                User(id=1, tg_id=OWNER_ID, timezone="Europe/Moscow"),
                User(id=2, tg_id=999, timezone="Europe/Moscow"),
            ]
        )
    ro = readonly_engine(db_path)
    app = create_app(create_sessionmaker(ro), TOKEN, OWNER_ID)
    async with TestClient(TestServer(app)) as client:
        yield Api(db, client)
    await ro.dispose()
    await rw.dispose()


def make_sub(
    name="Claude Code",
    unit="month",
    count=1,
    anchor=date(2026, 10, 16),
    next_charge=date(2026, 11, 16),
    user_id=1,
    **kw,
) -> Subscription:
    return Subscription(
        user_id=user_id,
        name=name,
        amount_minor=kw.pop("amount_minor", 2224),
        currency=kw.pop("currency", "USD"),
        period_unit=unit,
        period_count=count,
        anchor_date=anchor,
        next_charge_date=next_charge,
        **kw,
    )


# --- auth -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer wrong"},
        {"Authorization": f"Basic {TOKEN}"},
        {"Authorization": TOKEN},
        {"Authorization": "Bearer "},
    ],
)
async def test_bad_token_is_401(api, headers):
    for path in ("/api/v1/health", "/api/v1/subscriptions", "/api/v1/payments"):
        status, body = await api.get(path, headers=headers)
        assert status == 401
        assert body == {"error": "missing or invalid token"}


async def test_health_with_token(api):
    assert await api.get("/api/v1/health") == (200, {"status": "ok"})


async def test_writes_and_unknown_paths_are_json_errors(api):
    resp = await api.client.post("/api/v1/subscriptions", headers=AUTH)
    assert resp.status == 405
    assert "GET" in resp.headers["Allow"]
    assert await resp.json() == {"error": "Method Not Allowed"}
    assert await api.get("/api/v1/nope") == (404, {"error": "Not Found"})


async def test_api_cannot_write_to_db(tmp_path):
    db_path = tmp_path / "ro.db"
    rw = create_engine(f"sqlite+aiosqlite:///{db_path.as_posix()}")
    async with rw.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    ro = readonly_engine(db_path)
    try:
        with pytest.raises(OperationalError, match="readonly"):
            async with ro.begin() as conn:
                await conn.execute(text("INSERT INTO users (tg_id) VALUES (5)"))
    finally:
        await ro.dispose()
        await rw.dispose()


# --- subscriptions ----------------------------------------------------------------------


async def test_subscription_format(api):
    [sub_id] = await api.add(
        make_sub(created_at=datetime(2026, 10, 16, 9, 0), remind_offsets=None)
    )
    status, body = await api.get("/api/v1/subscriptions")
    assert status == 200
    assert body == [
        {
            "id": sub_id,
            "user_id": 1,
            "name": "Claude Code",
            "amount_minor": 2224,
            "currency": "USD",
            "period_unit": "month",
            "period_count": 1,
            "anchor_date": "2026-10-16",
            "next_charge_date": "2026-11-16",
            "status": "active",
            "auto_renew": True,
            "trial_end": None,
            "remind_offsets": None,
            "category": None,
            "note": None,
            "created_at": "2026-10-16T09:00:00Z",
            "archived_at": None,
        }
    ]


async def test_offsets_booleans_and_db_default_timestamp(api):
    await api.add(
        make_sub("A", remind_offsets=[], auto_renew=False),
        make_sub("B", remind_offsets=[7, 1], category="AI", note="work"),
    )
    _, body = await api.get("/api/v1/subscriptions")
    assert [(s["remind_offsets"], s["auto_renew"]) for s in body] == [([], False), ([7, 1], True)]
    assert all(type(s["auto_renew"]) is bool for s in body)
    # created_at filled by SQLite's CURRENT_TIMESTAMP, which is UTC
    assert all(re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", s["created_at"]) for s in body)


async def test_status_filter_and_owner_only(api):
    await api.add(
        make_sub("Active"),
        make_sub("Old", status="archived", archived_at=datetime(2026, 9, 1, 8, 30, 15, 123456)),
        make_sub("Someone else's", user_id=2),
        make_sub("Someone else's old", user_id=2, status="archived"),
    )
    _, active = await api.get("/api/v1/subscriptions")
    assert [s["name"] for s in active] == ["Active"]
    assert await api.get("/api/v1/subscriptions", status="active") == (200, active)
    _, archived = await api.get("/api/v1/subscriptions", status="archived")
    assert [s["name"] for s in archived] == ["Old"]
    assert archived[0]["archived_at"] == "2026-09-01T08:30:15Z"

    assert await api.get("/api/v1/subscriptions", status="trial") == (
        400,
        {"error": "'status' must be one of: active, archived"},
    )


async def test_unknown_user_is_404(tmp_path, now):
    db_path = tmp_path / "empty.db"
    rw = create_engine(f"sqlite+aiosqlite:///{db_path.as_posix()}")
    async with rw.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    ro = readonly_engine(db_path)
    app = create_app(create_sessionmaker(ro), TOKEN, OWNER_ID)
    try:
        async with TestClient(TestServer(app)) as client:
            for path, params in [
                ("/api/v1/subscriptions", {}),
                ("/api/v1/subscriptions/upcoming", {"until": "2026-12-01"}),
                ("/api/v1/payments", {"from": "2026-01-01", "to": "2026-12-31"}),
            ]:
                resp = await client.get(path, headers=AUTH, params=params)
                assert resp.status == 404
                assert "user not found" in (await resp.json())["error"]
    finally:
        await ro.dispose()
        await rw.dispose()


# --- upcoming ---------------------------------------------------------------------------


async def upcoming_dates(api, until: str) -> list[str]:
    status, body = await api.get("/api/v1/subscriptions/upcoming", until=until)
    assert status == 200
    return [i["charge_date"] for i in body]


@pytest.mark.parametrize(
    ("unit", "count", "anchor", "next_charge", "until", "expected"),
    [
        # Every 3 days, today included
        ("day", 3, date(2026, 10, 13), date(2026, 10, 16), "2026-10-25",
         ["2026-10-16", "2026-10-19", "2026-10-22", "2026-10-25"]),
        # Weekly over a month gives 4-5 entries and crosses into November
        ("week", 1, date(2026, 10, 16), date(2026, 10, 23), "2026-11-20",
         ["2026-10-23", "2026-10-30", "2026-11-06", "2026-11-13", "2026-11-20"]),
        # Anchor on the 31st: clamped in short months, back to 31 afterwards; crosses the year
        ("month", 2, date(2026, 8, 31), date(2026, 10, 31), "2027-05-01",
         ["2026-10-31", "2026-12-31", "2027-02-28", "2027-04-30"]),
        ("month", 1, date(2026, 9, 30), date(2026, 10, 30), "2027-03-31",
         ["2026-10-30", "2026-11-30", "2026-12-30", "2027-01-30", "2027-02-28", "2027-03-30"]),
        ("month", 1, date(2026, 1, 29), date(2026, 10, 29), "2027-03-01",
         ["2026-10-29", "2026-11-29", "2026-12-29", "2027-01-29", "2027-02-28"]),
        # Leap day: Feb 28 in common years, Feb 29 again in a leap year
        ("year", 1, date(2024, 2, 29), date(2027, 2, 28), "2028-03-01",
         ["2027-02-28", "2028-02-29"]),
        ("month", 1, date(2027, 12, 31), date(2028, 1, 31), "2028-03-31",
         ["2028-01-31", "2028-02-29", "2028-03-31"]),
    ],
)
async def test_upcoming_periods(api, unit, count, anchor, next_charge, until, expected):
    await api.add(make_sub(unit=unit, count=count, anchor=anchor, next_charge=next_charge))
    assert await upcoming_dates(api, until) == expected


async def test_upcoming_item_format_and_order(api):
    week_id, month_id = await api.add(
        make_sub("Gym", unit="week", next_charge=date(2026, 10, 23), amount_minor=150000,
                 currency="RUB", auto_renew=False),
        make_sub("Claude Code", next_charge=date(2026, 10, 23)),
    )
    await api.add(
        make_sub("Archived", next_charge=date(2026, 10, 20), status="archived"),
        make_sub("Not mine", user_id=2, next_charge=date(2026, 10, 20)),
    )
    _, body = await api.get("/api/v1/subscriptions/upcoming", until="2026-10-30")
    assert body == [
        {"subscription_id": week_id, "name": "Gym", "charge_date": "2026-10-23",
         "amount_minor": 150000, "currency": "RUB", "auto_renew": False},
        {"subscription_id": month_id, "name": "Claude Code", "charge_date": "2026-10-23",
         "amount_minor": 2224, "currency": "USD", "auto_renew": True},
        {"subscription_id": week_id, "name": "Gym", "charge_date": "2026-10-30",
         "amount_minor": 150000, "currency": "RUB", "auto_renew": False},
    ]


async def test_overdue_manual_charge_is_not_upcoming(api):
    # Unpaid since 10.10: the bot waits for "Оплатил", the next one is still 10.11
    await api.add(make_sub(anchor=date(2026, 9, 10), next_charge=date(2026, 10, 10),
                           auto_renew=False))
    assert await upcoming_dates(api, "2026-11-30") == ["2026-11-10"]


async def test_until_before_today_is_empty(api):
    await api.add(make_sub(unit="day", next_charge=date(2026, 10, 16)))
    assert await upcoming_dates(api, "2026-10-15") == []
    assert await upcoming_dates(api, "2026-10-16") == ["2026-10-16"]


@pytest.mark.parametrize(("tz", "expected"), [
    ("Europe/Moscow", []),  # 21:30 UTC is already 17.10 in Moscow
    ("America/New_York", ["2026-10-16"]),  # still 16.10 there
])
async def test_today_is_in_users_timezone(api, now, tz, expected):
    async with api.db() as session, session.begin():
        user = await session.get(User, 1)
        user.timezone = tz
    await api.add(make_sub(unit="day", anchor=date(2026, 10, 15), next_charge=date(2026, 10, 16),
                           auto_renew=False))
    now["now"] = datetime(2026, 10, 16, 21, 30, tzinfo=UTC)
    assert await upcoming_dates(api, "2026-10-16") == expected


@pytest.mark.parametrize(("params", "error"), [
    ({}, "'until' is required"),
    ({"until": "16.11.2026"}, "'until' must be a date in YYYY-MM-DD format"),
    ({"until": "2031-12-31"}, "'until' must be within 1830 days from today"),
])
async def test_upcoming_bad_until(api, params, error):
    assert await api.get("/api/v1/subscriptions/upcoming", **params) == (400, {"error": error})


async def test_upcoming_matches_real_renewals(h, now):
    """The dates promised by the API are the dates the scheduler actually charges."""
    await onboard(h)
    async with h.sessionmaker() as session, session.begin():
        owner = await session.scalar(select(User))
        session.add_all([
            make_sub("Monthly 31", anchor=date(2026, 8, 31), next_charge=date(2026, 10, 31),
                     user_id=owner.id),
            make_sub("Biweekly", unit="week", count=2, anchor=date(2026, 10, 10),
                     next_charge=date(2026, 10, 24), user_id=owner.id),
        ])
    db_path = Path(h.sessionmaker.kw["bind"].url.database)
    ro = readonly_engine(db_path)
    app = create_app(create_sessionmaker(ro), TOKEN, OWNER_ID)
    try:
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/subscriptions/upcoming", headers=AUTH,
                                    params={"until": "2027-10-16"})
            promised = sorted((i["subscription_id"], i["charge_date"]) for i in await resp.json())
    finally:
        await ro.dispose()

    day = NOW
    while day.date() <= date(2027, 10, 16):
        now["now"] = day
        await h.tick(day)
        day += timedelta(days=1)
    charged = sorted(
        (p.subscription_id, p.charge_date.isoformat())
        for p in await h.payments()
        if p.charge_date > date(2026, 10, 16)
    )
    assert len(promised) == 12 + 26
    assert charged == promised


# --- payments ---------------------------------------------------------------------------


async def test_payments_bounds_are_inclusive(api):
    mine, archived, other = await api.add(
        make_sub("Mine"), make_sub("Archived", status="archived"), make_sub("Other", user_id=2)
    )
    ids = await api.add(*[
        Payment(subscription_id=sid, charge_date=d, amount_minor=100, currency="USD")
        for sid, d in [
            (mine, date(2026, 10, 1)),
            (mine, date(2026, 10, 2)),
            (archived, date(2026, 10, 15)),
            (other, date(2026, 10, 15)),
            (mine, date(2026, 10, 31)),
            (mine, date(2026, 11, 1)),
        ]
    ])
    status, body = await api.get("/api/v1/payments", **{"from": "2026-10-02", "to": "2026-10-31"})
    assert status == 200
    assert body == [
        {"id": ids[1], "subscription_id": mine, "charge_date": "2026-10-02",
         "amount_minor": 100, "currency": "USD"},
        {"id": ids[2], "subscription_id": archived, "charge_date": "2026-10-15",
         "amount_minor": 100, "currency": "USD"},
        {"id": ids[4], "subscription_id": mine, "charge_date": "2026-10-31",
         "amount_minor": 100, "currency": "USD"},
    ]
    _, single_day = await api.get("/api/v1/payments", **{"from": "2026-10-01", "to": "2026-10-01"})
    assert [p["id"] for p in single_day] == [ids[0]]


@pytest.mark.parametrize(("params", "error"), [
    ({"to": "2026-10-31"}, "'from' is required"),
    ({"from": "2026-10-01"}, "'to' is required"),
    ({"from": "2026-10-01", "to": "tomorrow"}, "'to' must be a date in YYYY-MM-DD format"),
    ({"from": "2026-11-01", "to": "2026-10-01"}, "'from' must not be later than 'to'"),
])
async def test_payments_bad_params(api, params, error):
    assert await api.get("/api/v1/payments", **params) == (400, {"error": error})
