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

## CI/CD
`.github/workflows/ci-cd.yml`:
- любой push и PR — тесты и ruff в Docker-образе (`--target test`, Python 3.12);
- push в `main` (или ручной запуск) — сборка образа `ghcr.io/two-lifes/subs_tracker:<sha>`
  и деплой на сервер по SSH через `deploy/deploy.sh`.

`deploy.sh` на сервере: скачивает образ → останавливает бота → копирует БД в `backups/`
(хранятся 10 последних) → запускает новую версию → ждёт в логе `Run polling` до 90 с.
Если бот не поднялся — возвращает БД из копии и запускает предыдущую версию, job падает.

### Настройка сервера (один раз)
1. Docker, пользователь `deploy` в группе `docker`, каталог `/opt/subs_tracker` (владелец `deploy`).
2. `/opt/subs_tracker/.env` — `BOT_TOKEN`, `ALLOWED_USER_ID`, `PROXY_URL` (см. `.env.example`).
3. `mkdir /opt/subs_tracker/data && sudo chown 1000:1000 /opt/subs_tracker/data` —
   бот в контейнере работает под uid 1000.
4. Под `deploy`: `docker login ghcr.io` с токеном `read:packages`.

### Настройка GitHub (Settings → Secrets and variables → Actions)
Secrets:
- `SSH_HOST`, `SSH_USER` (`deploy`), `SSH_PORT` (если не 22);
- `SSH_KEY` — приватный ключ, публичная часть в `~deploy/.ssh/authorized_keys`;
- `SSH_KNOWN_HOSTS` — вывод `ssh-keyscan -p <порт> <хост>`.

Variables:
- `DEPLOY_ENABLED` = `true` — включает деплой (без него выполняются только тесты);
- `DEPLOY_DIR` — если каталог не `/opt/subs_tracker`.

### Ручные операции на сервере (в `/opt/subs_tracker`)
- Логи: `docker compose logs -f --tail 100`
- Откат: `IMAGE_TAG=<sha> docker compose up -d` (текущая версия — в `.deployed_tag`)
- Восстановить БД: `docker compose stop; cp backups/<файл>.db data/subs.db; docker compose up -d`
