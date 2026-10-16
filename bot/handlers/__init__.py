from aiogram import Router

from bot.handlers import add, fallback, notify_actions, start, subs
from bot.handlers.menu import menu_router


def build_router() -> Router:
    router = Router(name="root")
    router.include_router(menu_router)
    router.include_routers(start.router, add.router, subs.router, notify_actions.router)
    # Must stay last: catches whatever no other router handled
    router.include_router(fallback.router)
    return router
