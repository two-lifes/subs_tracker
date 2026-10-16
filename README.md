# subs_tracker

Личный Telegram-бот: учёт подписок, расчёт дат списания, напоминания.

## Локальный запуск
```powershell
python -m venv .venv; .\.venv\Scripts\python -m pip install -e ".[dev]"
copy .env.example .env   # заполнить BOT_TOKEN, ALLOWED_USER_ID, PROXY_URL
.\.venv\Scripts\python -m bot
```
Тесты: `.\.venv\Scripts\python -m pytest`, линтер: `.\.venv\Scripts\ruff check .`

Локальный бот и бот на сервере с одним токеном одновременно работать не могут —
перед запуском одного остановите другой.

## Read-only HTTP API
For other services (e.g. a finance bot). The API runs in the bot's process and only reads the
same SQLite DB: its connections are opened with `mode=ro`, so writing is physically impossible.
It never blocks the bot: it does not take the bot's `db_lock`, and in SQLite's default journal
mode readers proceed while the bot holds a write transaction (a commit waits for readers only
for milliseconds, covered by the 5 s busy timeout). WAL is not needed since there is no second
process (and would complicate `deploy.sh` backups, which copy only `subs.db`).

### `.env` variables
| Variable | Default | Meaning |
|---|---|---|
| `API_TOKEN` | — | Bearer token. Empty disables the API. Generate: `python -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `API_HOST` | `127.0.0.1` | Listen address. `deploy/docker-compose.yml` sets `0.0.0.0` inside the container |
| `API_PORT` | `8080` | Listen port |
| `API_TELEGRAM_ID` | `ALLOWED_USER_ID` | Telegram id whose data is served; mapped to `users.id` via `users.tg_id`. Until that user has sent `/start`, data endpoints return 404 |

"Today" is computed in the user's time zone chosen in the bot (`users.timezone`, editable in
Settings), exactly as for reminders, so there is no separate time zone variable.

### Running with the bot
Locally: put `API_TOKEN` into `.env` and start the bot as usual (`.\.venv\Scripts\python -m bot`);
the log shows `API listening on 127.0.0.1:8080`.

On the server: add `API_TOKEN` to `/opt/subs_tracker/.env`; the next deploy (or
`docker compose up -d`) starts the API. No port is published to the host: the API is reachable
only from containers on the docker network `subs_api` at `http://subs-api:8080`. The finance
bot's `docker-compose.yml` joins it:
```yaml
services:
  finance:
    # ...
    networks: [default, subs_api]
networks:
  subs_api:
    external: true
```
The network is created by this bot's `docker compose up`, so start this bot first.
Check from the server:
`docker run --rm --network subs_api curlimages/curl -s -H "Authorization: Bearer $TOKEN" http://subs-api:8080/api/v1/health`

If the finance bot ever runs on another server, do not expose the port directly. Use either
- a reverse proxy with HTTPS, e.g. Caddy with automatic Let's Encrypt certificates
  (`api.example.com { reverse_proxy 127.0.0.1:8080 }`, publish the port as
  `127.0.0.1:8080:8080`), ideally with a firewall rule allowing only the finance server's IP; or
- a tunnel with no public port: WireGuard between the servers, or
  `autossh -N -L 8080:127.0.0.1:8080 deploy@<vds>` on the finance server.

### Endpoints
Every request is a `GET` with `Authorization: Bearer <API_TOKEN>`; a missing or invalid token
gives `401`. Responses are JSON arrays (an object for health). Errors are
`{"error": "description"}` with status `400` (bad parameters), `401`, `404` (unknown user or
path), `405` (not GET) or `500`. Dates are `YYYY-MM-DD`; amounts are integer minor units
(`2224` USD = 22.24 USD).

| Endpoint | Parameters | Returns |
|---|---|---|
| `/api/v1/health` | — | `{"status": "ok"}` |
| `/api/v1/subscriptions` | `status=active\|archived` (default `active`, anything else is 400) | Subscriptions, by `id` |
| `/api/v1/subscriptions/upcoming` | `until=YYYY-MM-DD` (required, at most 1830 days ahead) | Charges of active subscriptions from today to `until` inclusive, by date |
| `/api/v1/payments` | `from`, `to` (required, inclusive, `from <= to`) | Payments of all subscriptions, archived included, by date |

`upcoming` starts at `next_charge_date` and steps with the same function the bot uses to
advance it after a charge (`anchor + k * period`: the 31st becomes Feb 28/29, then the 31st
again). A short period yields several entries. An overdue manual charge (before today, waiting
for "✅ Оплатил") is not listed; the charges after it are.

```bash
TOKEN=...; API=http://127.0.0.1:8080/api/v1
curl -H "Authorization: Bearer $TOKEN" "$API/health"
curl -H "Authorization: Bearer $TOKEN" "$API/subscriptions?status=archived"
curl -H "Authorization: Bearer $TOKEN" "$API/subscriptions/upcoming?until=2026-12-31"
curl -H "Authorization: Bearer $TOKEN" "$API/payments?from=2026-10-01&to=2026-10-31"
```

`GET /subscriptions`:
```json
[{"id": 1, "user_id": 1, "name": "Claude Code", "amount_minor": 2224, "currency": "USD",
  "period_unit": "month", "period_count": 1, "anchor_date": "2026-10-16",
  "next_charge_date": "2026-11-16", "status": "active", "auto_renew": true,
  "trial_end": null, "remind_offsets": null, "category": null, "note": null,
  "created_at": "2026-10-16T09:00:00Z", "archived_at": null}]
```
`created_at`/`archived_at` are UTC with `Z`. `remind_offsets` is returned as stored: `null`
(use the user's defaults), `[]` (no reminders) or a list of days before the charge.

`GET /subscriptions/upcoming?until=2026-12-31`:
```json
[{"subscription_id": 1, "name": "Claude Code", "charge_date": "2026-11-16",
  "amount_minor": 2224, "currency": "USD", "auto_renew": true},
 {"subscription_id": 1, "name": "Claude Code", "charge_date": "2026-12-16",
  "amount_minor": 2224, "currency": "USD", "auto_renew": true}]
```

`GET /payments?from=2026-10-01&to=2026-10-31`:
```json
[{"id": 1, "subscription_id": 1, "charge_date": "2026-10-16", "amount_minor": 2224, "currency": "USD"}]
```

`GET /subscriptions?status=trial` → `400 {"error": "'status' must be one of: active, archived"}`

## CI/CD
`.github/workflows/ci-cd.yml`:
- любой push и PR — тесты и ruff в Docker-образе (`--target test`, Python 3.12);
- push в `main` (или ручной запуск) — сборка образа `ghcr.io/two-lifes/subs_tracker:<sha>`
  и деплой на сервер по SSH через `deploy/deploy.sh`.

`deploy.sh` на сервере: скачивает образ → останавливает бота → копирует БД в `backups/`
(хранятся 10 последних) → запускает новую версию → ждёт в логе `Run polling` до 90 с.
Если бот не поднялся — возвращает БД из копии и запускает предыдущую версию, job падает.

CI зашивает в образ `APP_VERSION` (sha) и `APP_TITLE` (заголовок коммита). При старте бот
записывает новую версию в таблицу `deployments` и присылает «🔄 Бот обновлён». При откате
`deploy.sh` запускает старую версию с `FAILED_VERSION`, и она сообщает «❌ Обновление … не
запустилось». История — в «Настройки → 🔄 Обновления». Локальный запуск ничего не записывает.

### Настройка сервера (один раз)
1. Docker, пользователь `deploy` в группе `docker`, каталог `/opt/subs_tracker` (владелец `deploy`).
2. `/opt/subs_tracker/.env` — `BOT_TOKEN`, `ALLOWED_USER_ID`, `PROXY_URL`, при необходимости
   `API_TOKEN` (см. `.env.example`).
3. `mkdir /opt/subs_tracker/data && sudo chown 1000:1000 /opt/subs_tracker/data` —
   бот в контейнере работает под uid 1000.
4. Под `deploy`: `docker login ghcr.io` с токеном `read:packages`.

### Настройка GitHub (Settings → Secrets and variables → Actions)
Secrets:
- `SSH_HOST`, `SSH_USER` (`deploy`), `SSH_PORT` (если не 22);
- `SSH_PRIVATE_KEY` — приватный ключ, публичная часть в `~deploy/.ssh/authorized_keys`;
- `SSH_KNOWN_HOSTS` — вывод `ssh-keyscan -p <порт> <хост>`.

Variables:
- `DEPLOY_ENABLED` = `true` — включает деплой (без него выполняются только тесты);
- `DEPLOY_DIR` — если каталог не `/opt/subs_tracker`.

### Ручные операции на сервере (в `/opt/subs_tracker`)
- Логи: `docker compose logs -f --tail 100`
- Откат: `IMAGE_TAG=<sha> docker compose up -d` (текущая версия — в `.deployed_tag`)
- Восстановить БД: `docker compose stop; cp backups/<файл>.db data/subs.db; docker compose up -d`
