"""Runs the real dispatcher against a temporary DB and a fake Telegram network."""

import asyncio
import itertools
from datetime import datetime
from functools import cache

from aiogram import Bot
from sqlalchemy import select

from bot.app import build_dispatcher
from bot.db.models import Base, Notification, Payment, Subscription
from bot.db.session import create_engine, create_sessionmaker
from bot.scheduler import tick
from tests.tg_fakes import OWNER_ID, FakeSession, callback_update, text_update

TZ = "Europe/Moscow"


class Harness:
    def __init__(self, dp, bot, session, sessionmaker):
        self.dp, self.bot, self.net, self.sessionmaker = dp, bot, session, sessionmaker
        self.db_lock = asyncio.Lock()

    async def feed(self, update) -> None:
        await self.dp.feed_update(
            self.bot, update, sessionmaker=self.sessionmaker, db_lock=self.db_lock
        )

    async def text(self, text: str, user_id: int = OWNER_ID) -> None:
        await self.feed(text_update(text, user_id))

    async def tap(self, data: str, inaccessible: bool = False) -> None:
        await self.feed(callback_update(data, inaccessible=inaccessible))

    async def subs(self) -> list[Subscription]:
        return await self._all(Subscription)

    async def payments(self) -> list[Payment]:
        return await self._all(Payment)

    async def notifications(self) -> list[Notification]:
        return await self._all(Notification)

    async def _all(self, model):
        async with self.sessionmaker() as session:
            return list(await session.scalars(select(model).order_by(model.id)))

    async def tick(self, now: datetime) -> None:
        await tick(self.sessionmaker, self.bot, now, self.db_lock)


@cache
def shared_dispatcher():
    # Routers are module-level and can be attached to only one dispatcher per process
    return build_dispatcher(OWNER_ID)


# FSM state is keyed by bot id, so a fresh bot id per test isolates dialog state
_bot_ids = itertools.count(100)


async def make_harness(db_path) -> tuple[Harness, object]:
    # A file DB: every update opens its own connection, and :memory: is per-connection
    engine = create_engine(f"sqlite+aiosqlite:///{db_path.as_posix()}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    net = FakeSession()
    bot = Bot(token=f"{next(_bot_ids)}:TEST", session=net)
    return Harness(shared_dispatcher(), bot, net, create_sessionmaker(engine)), engine


async def onboard(h: Harness) -> None:
    await h.text("/start")
    await h.tap(f"tz:{TZ}")
    await h.tap("cur:USD")
