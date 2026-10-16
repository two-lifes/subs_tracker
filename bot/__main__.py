import asyncio
import contextlib
import logging
import sys

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiohttp import web
from alembic import command
from alembic.config import Config as AlembicConfig

from bot.api import create_app
from bot.app import build_dispatcher
from bot.clock import utcnow
from bot.config import Config, ConfigError, load_config
from bot.db.session import create_engine, create_sessionmaker
from bot.scheduler import run_scheduler
from bot.services.deployments import record_startup

log = logging.getLogger(__name__)


def run_migrations(config: Config) -> None:
    alembic_cfg = AlembicConfig("alembic.ini")
    alembic_cfg.set_main_option("sqlalchemy.url", config.db_url)
    # Otherwise alembic.ini's logging config would silence the bot's INFO logs
    alembic_cfg.attributes["configure_logger"] = False
    command.upgrade(alembic_cfg, "head")


async def start_api(config: Config) -> web.AppRunner | None:
    if not config.api_token:
        log.info("API disabled: API_TOKEN is not set")
        return None
    engine = create_engine(config.db_readonly_url)
    app = create_app(create_sessionmaker(engine), config.api_token, config.api_telegram_id)
    app.on_cleanup.append(lambda _app: engine.dispose())
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, config.api_host, config.api_port).start()
    log.info("API listening on %s:%s", config.api_host, config.api_port)
    return runner


async def main(config: Config) -> None:
    engine = create_engine(config.db_url)
    sessionmaker = create_sessionmaker(engine)
    async with sessionmaker() as db, db.begin():
        await record_startup(
            db, config.app_version, config.app_title, config.failed_version, utcnow()
        )
    log.info("Version: %s", config.app_version or "local")

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

    api = await start_api(config)
    scheduler = asyncio.create_task(run_scheduler(sessionmaker, bot, db_lock), name="scheduler")
    try:
        await dp.start_polling(bot)
    finally:
        if api is not None:
            await api.cleanup()
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
