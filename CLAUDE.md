# subs_tracker

Personal Telegram bot (aiogram 3) that tracks subscriptions and reminds about charges.
Plan: see README / project plan. Single user (whitelist by `ALLOWED_USER_ID`).

## Commands (Windows, from project root)
- Install: `python -m venv .venv; .\.venv\Scripts\python -m pip install -e ".[dev]"`
- Run bot: `.\.venv\Scripts\python -m bot` (applies migrations on start; needs `.env`, see `.env.example`)
- Tests: `.\.venv\Scripts\python -m pytest`
- Lint: `.\.venv\Scripts\ruff check .`
- New migration: `.\.venv\Scripts\alembic revision --autogenerate -m "..."` (uses `DB_PATH`)
- Tests on Python 3.12 in Docker: `docker build --target test .`
- Run in Docker: `docker compose up -d --build` (DB in `./data`). Don't run it together with the
  local bot — two pollers with one token conflict.

## Testing dialogs
`tests/test_flows.py` feeds real updates into the dispatcher with a fake network session
(`tests/tg_fakes.py`). Routers are module-level, so the dispatcher is built once per process;
the sessionmaker is passed as workflow data (`dp["sessionmaker"]`).

## Conventions
- Money is stored as integer minor units + ISO currency code; never float.
- Charge dates are derived from `anchor_date` + k periods (no drift); logic lives in `bot/services/billing.py`.
- Pure logic goes to `bot/services/`, Telegram code to `bot/handlers/`; user-facing text is Russian.
- Get the current time only via `bot.clock.utcnow()` / `user_today()`; tests swap it with `set_clock`.
- SQLite drops tzinfo: store every datetime as UTC, read back with `as_utc()`.
- All bot-initiated messages go through the `notifications` table (`bot/services/notifications.py`
  plans, `bot/scheduler.py` delivers); never send reminders directly. Deploy notices likewise go
  through the `deployments` table (`bot/services/deployments.py`).
