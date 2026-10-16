import asyncio
import contextlib
import logging
import sys

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from alembic import command
from alembic.config import Config as AlembicConfig

from bot.app import build_dispatcher
from bot.config import Config, ConfigError, load_config
from bot.db.session import create_engine, create_sessionmaker
from bot.scheduler import run_scheduler

log = logging.getLogger(__name__)


def run_migrations(config: Config) -> None:
    alembic_cfg = AlembicConfig("alembic.ini")
    alembic_cfg.set_main_option("sqlalchemy.url", config.db_url)
    # Otherwise alembic.ini's logging config would silence the bot's INFO logs
    alembic_cfg.attributes["configure_logger"] = False
    command.upgrade(alembic_cfg, "head")


async def main(config: Config) -> None:
    engine = create_engine(config.db_url)
    sessionmaker = create_sessionmaker(engine)

    session = AiohttpSession(proxy=config.proxy_url) if config.proxy_url else None
    bot = Bot(
        token=config.bot_token,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = build_dispatcher(config.allowed_user_id)
    dp["sessionmaker"] = sessionmaker
    db_lock = asyncio.Lock()
    dp["db_lock"] = db_lock

    scheduler = asyncio.create_task(run_scheduler(sessionmaker, bot, db_lock), name="scheduler")
    try:
        await dp.start_polling(bot)
    finally:
        scheduler.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await scheduler
        await engine.dispose()


def run() -> None:
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stdout,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        config = load_config()
    except ConfigError as exc:
        log.error("Configuration error: %s", exc)
        sys.exit(1)
    # Alembic's async env calls asyncio.run itself, so it must run outside our event loop
    run_migrations(config)
    asyncio.run(main(config))


if __name__ == "__main__":
    run()
