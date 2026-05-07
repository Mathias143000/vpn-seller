# vpn-seller

Production-ready бот-магазин для продажи VPN-доступов. Основной канал и админка работают в Telegram через `aiogram 3`, пользовательские продажи также поддержаны во VK и WhatsApp. Источник истины - база данных; XLSX используется только для импорта, экспорта и отчетности.

## Содержание
- [Что умеет проект](#что-умеет-проект)
- [Архитектура](#архитектура)
- [Структура проекта](#структура-проекта)
- [Зависимости](#зависимости)
- [Переменные окружения](#переменные-окружения)
- [Быстрый запуск](#быстрый-запуск)
- [Docker запуск](#docker-запуск)
- [Развертывание на сервере через Git](#развертывание-на-сервере-через-git)
- [База данных и миграции](#база-данных-и-миграции)
- [Каналы: Telegram, VK, WhatsApp](#каналы-telegram-vk-whatsapp)
- [Платежи](#платежи)
- [Выдача доступов](#выдача-доступов)
- [Hiddify Manager API v2](#hiddify-manager-api-v2)
- [MTProxy и superkey](#mtproxy-и-superkey)
- [Промокоды и цены](#промокоды-и-цены)
- [Админка](#админка)
- [XLSX импорт и экспорт](#xlsx-импорт-и-экспорт)
- [Webhook endpoints](#webhook-endpoints)
- [Health endpoints](#health-endpoints)
- [Фоновые процессы](#фоновые-процессы)
- [Безопасность](#безопасность)
- [Runbook по контурам](#runbook-по-контурам)
- [Проверки и тесты](#проверки-и-тесты)
- [Чистота репозитория](#чистота-репозитория)

## Что умеет проект
- Показывает тарифы на 30, 90, 180 и 365 дней.
- Создает заказ только если есть доступная выдача: локальный склад ключей или активный Hiddify-сервер.
- Поддерживает ручную проверку оплаты через Donate.Stream.
- Имеет `FakePaymentProvider` для локальной разработки и тестов.
- Резервирует локальный ключ на время оплаты и безопасно снимает просроченный резерв.
- Выдает ровно один доступ после подтвержденной оплаты.
- Не подтверждает оплату пользовательским callback или переходом по ссылке.
- Доставляет доступ через post-commit очередь `delivery_jobs`.
- Поддерживает повторную доставку и replacement key из админки.
- Хранит аудит действий и статусных переходов.
- Подключает несколько Hiddify-панелей через Telegram-админку.
- Генерирует доступ через Hiddify Manager API v2.
- Поддерживает MTProxy/MTProto выдачу на наименее загруженном активном сервере.
- Поддерживает superkey: один subscription URL, агрегирующий активные серверы.
- Хранит реквизиты магазина в базе и позволяет менять Donate.Stream URL и контакты поддержки через `/admin_settings`.
- Поддерживает точечные админ-уведомления и broadcast через Telegram-админку.
- Импортирует и экспортирует ключи через XLSX.
- Импортирует Hiddify-серверы через XLSX с preview.
- Работает в Telegram, VK и WhatsApp пользовательских каналах.
- Имеет health/readiness endpoints и webhook endpoints.
- Имеет тесты для платежей, заказов, выдачи, импорта/экспорта, Hiddify, VK, WhatsApp и delivery jobs.

## Архитектура
Код разделен по слоям:
- `handlers` - тонкие Telegram handlers.
- `services` - бизнес-логика: заказы, платежи, выдача, Hiddify, доставка, промокоды, pricing, настройки магазина.
- `repositories` - доступ к данным через async SQLAlchemy.
- `web` - FastAPI endpoints для health, webhook, файлов и subscriptions.
- `db` - модели SQLAlchemy и Alembic миграции.
- `keyboards`, `states`, `middlewares`, `filters` - инфраструктура aiogram.

Главные инварианты:
- База данных - единственный источник истины.
- Один VPN-ключ не может быть продан дважды.
- Выдача идет только после подтвержденной оплаты или ручного admin-confirmation для Donate.Stream.
- Повторные webhook и ручные подтверждения должны быть идемпотентными.
- Ключ не отправляется пользователю до коммита транзакции выдачи.
- Если оплата прошла, но доступ не выдан, заказ переходит в `paid_but_not_issued`.
- Все важные переходы пишутся в `audit_logs`.

## Структура проекта
```text
app/
  db/                    SQLAlchemy models, Alembic migrations, session helpers
  filters/               aiogram filters
  handlers/              Telegram user/admin handlers
  keyboards/             Telegram inline keyboards
  middlewares/           auth/admin/logging middlewares
  repositories/          database repositories
  services/              business services
    imports/             XLSX import/export services
    payments/            payment provider abstraction and providers
  states/                aiogram FSM states
  web/                   FastAPI routers
assets/                  XLSX templates, sample export, setup PDF, static assets
content/                 editable messages and pricing JSON
data/                    local SQLite database, development only
scripts/                 helper scripts
tests/                   automated tests
alembic.ini              Alembic configuration
docker-compose.yml       local/server Docker Compose
Dockerfile               production image
pyproject.toml           package metadata and dependencies
requirements.txt         runtime dependency list
requirements-dev.txt     dev/test dependency list
```

Папки `docs/` и `vpn_seller.egg-info/` удалены из исходника: их важное содержимое перенесено сюда. `vpn_seller.egg-info` является производным артефактом установки пакета и не нужен в чистом проекте.

## Зависимости
Целевой runtime:
- Python `3.11+`
- PostgreSQL в production
- SQLite только для локальной разработки

Основные runtime-зависимости:
- `aiogram`
- `fastapi`
- `uvicorn[standard]`
- `SQLAlchemy`
- `alembic`
- `asyncpg`
- `aiosqlite`
- `cryptography`
- `httpx`
- `openpyxl`
- `pydantic-settings`
- `python-multipart`

Установка из package metadata:
```powershell
python -m pip install -e ".[dev]"
```

Установка через requirements:
```powershell
python -m pip install -r requirements-dev.txt
```

## Переменные окружения
Все основные значения лежат в `.env.example`.

Базовые:
```env
APP_ENV=development
APP_HOST=0.0.0.0
APP_PORT=8000
APP_MODE=web
APP_BASE_URL=http://localhost:18080
APP_BIND_PORT=18080
LOG_LEVEL=INFO
```

Telegram:
```env
BOT_TOKEN=change-me
BOT_WEBHOOK_SECRET=telegram-webhook-secret
ADMIN_IDS=
SUPPORT_USERNAME=LDZHR
SUPPORT_URL=
```

`SUPPORT_USERNAME` и `SUPPORT_URL` - fallback/bootstrap значения. В рабочем магазине контакты поддержки лучше задавать из Telegram-админки:
```text
/admin_settings support_username username
/admin_settings support_url https://...
```

VK:
```env
VK_GROUP_ID=
VK_GROUP_TOKEN=
VK_CONFIRMATION_TOKEN=
VK_CALLBACK_SECRET=
VK_API_VERSION=5.199
```

WhatsApp:
```env
WHATSAPP_PHONE_NUMBER_ID=
WHATSAPP_ACCESS_TOKEN=
WHATSAPP_VERIFY_TOKEN=
WHATSAPP_APP_SECRET=
WHATSAPP_API_VERSION=v23.0
```

База, шифрование, резервы:
```env
DATABASE_URL=sqlite+aiosqlite:///./data/app.db
ENCRYPTION_KEY=change-me
DEFAULT_LOW_STOCK_THRESHOLD=5
RESERVATION_TTL_MINUTES=15
```

Платежи и доставка:
```env
PAYMENT_PROVIDER=donate_stream
PAYMENT_RECONCILIATION_MINUTES=15
PAYMENT_STALE_PENDING_MINUTES=30
DELIVERY_RETRY_SECONDS=30
DELIVERY_MAX_ATTEMPTS=5
DONATE_STREAM_URL=https://lk.donate.stream/
```

`DONATE_STREAM_URL` is a fallback/bootstrap value. For the live shop, set the actual Donate.Stream URL from Telegram admin:
```text
/admin_settings donate_url https://...
```

Контент и цены:
```env
CONTENT_FILE=content/messages.json
PLAN_PRICING_FILE=content/pricing.json
APPLY_PLAN_PRICING_ON_STARTUP=true
MIN_ORDER_AMOUNT=1
SERVER_MARKUP_PERCENT=0
SUPERKEY_MARKUP_PERCENT=50
```

Hiddify мониторинг:
```env
HIDDIFY_USAGE_SNAPSHOT_INTERVAL_MINUTES=60
HIDDIFY_USAGE_MONTHLY_WINDOW_DAYS=30
HIDDIFY_ACTIVE_USERS_ALERT_PERCENT=85
HIDDIFY_AVERAGE_MONTHLY_USAGE_ALERT_GB=800
HIDDIFY_ACTIVE_USERS_ALERT_PERCENT_BY_COUNTRY=
HIDDIFY_AVERAGE_MONTHLY_USAGE_ALERT_GB_BY_COUNTRY=NL=800,DE=1200,US=2000
HIDDIFY_ALERT_COOLDOWN_MINUTES=1440
```

## Быстрый запуск
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

Для локального Telegram polling:
```env
APP_MODE=polling
DATABASE_URL=sqlite+aiosqlite:///./data/app.db
PAYMENT_PROVIDER=fake
BOT_TOKEN=<local-bot-token>
ADMIN_IDS=<your-telegram-id>
```

Запуск:
```powershell
python -m app.main --polling
```

Web app для health, webhook и subscription endpoints:
```powershell
python -m app.main
```

## Docker запуск
One-line запуск:
```powershell
docker compose up -d --build
```

Compose поднимает:
- `db` - PostgreSQL 16, внутри Docker network.
- `bot` - Telegram long polling worker.
- `web` - FastAPI app на `8000` внутри контейнера.

Host port:
```text
${APP_BIND_PORT:-18080}:8000
```

Проверка compose:
```powershell
docker compose config -q
```

Применение миграций:
```powershell
docker compose run --rm web alembic upgrade head
```

Production webhook mode:
```powershell
docker compose up -d --build db web
```

Не запускайте polling-worker `bot` и Telegram webhook одновременно для одного `BOT_TOKEN`.

## Развертывание на сервере через Git
Ниже пример для чистого Ubuntu-сервера и поддомена `bot.super-lemming.online`.

### 1. Подготовить репозиторий локально
Если проект еще не является git-репозиторием:
```powershell
git init
git add .
git commit -m "Initial vpn-seller release"
git branch -M main
git remote add origin <git-repository-url>
git push -u origin main
```

Если репозиторий уже создан, но remote не добавлен:
```powershell
git remote add origin <git-repository-url>
git push -u origin main
```

Перед push убедитесь, что `.env`, локальная БД, cache-директории и реальные XLSX с ключами не попали в индекс:
```powershell
git status --short
```

### 2. DNS для поддомена
В панели DNS домена `super-lemming.online` создайте запись:
```text
Type: A
Host: bot
Value: <server-ipv4>
TTL: Auto или 300
```

Если у сервера есть IPv6, можно дополнительно добавить:
```text
Type: AAAA
Host: bot
Value: <server-ipv6>
TTL: Auto или 300
```

Проверка после обновления DNS:
```bash
dig +short bot.super-lemming.online
```

### 3. Подготовить пустой сервер
Подключитесь по SSH:
```bash
ssh root@<server-ip>
```

Установите базовые пакеты:
```bash
apt update
apt install -y ca-certificates curl git nginx certbot python3-certbot-nginx
```

Установите Docker Engine и Compose plugin:
```bash
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
. /etc/os-release
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $VERSION_CODENAME stable" > /etc/apt/sources.list.d/docker.list
apt update
apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
docker version
docker compose version
```

### 4. Склонировать проект
```bash
mkdir -p /opt/vpn-seller
cd /opt
git clone <git-repository-url> vpn-seller
cd /opt/vpn-seller
```

Если репозиторий приватный, используйте deploy key или personal access token по правилам вашего Git-хостинга.

### 5. Создать `.env` на сервере
```bash
cp .env.example .env
nano .env
```

Минимальный серверный `.env` для теста всех трех контуров:
```env
APP_ENV=staging
APP_MODE=web
APP_HOST=0.0.0.0
APP_PORT=8000
APP_BASE_URL=https://bot.super-lemming.online
APP_BIND_PORT=18080
LOG_LEVEL=INFO

BOT_TOKEN=<telegram-bot-token>
BOT_WEBHOOK_SECRET=<long-random-telegram-webhook-secret>
ADMIN_IDS=<your-telegram-id>
SUPPORT_USERNAME=<support-telegram-username>
SUPPORT_URL=

# Раскомментируйте VK_GROUP_ID и впишите число, если включаете VK.
VK_GROUP_ID=<vk-group-id>
VK_GROUP_TOKEN=<vk-group-token>
VK_CONFIRMATION_TOKEN=<vk-confirmation-token>
VK_CALLBACK_SECRET=<vk-callback-secret>
VK_API_VERSION=5.199

WHATSAPP_PHONE_NUMBER_ID=<whatsapp-phone-number-id>
WHATSAPP_ACCESS_TOKEN=<whatsapp-access-token>
WHATSAPP_VERIFY_TOKEN=<whatsapp-verify-token>
WHATSAPP_APP_SECRET=<whatsapp-app-secret>
WHATSAPP_API_VERSION=v23.0

ENCRYPTION_KEY=<long-random-encryption-key>
PAYMENT_PROVIDER=donate_stream
DONATE_STREAM_URL=<fallback-donate-stream-url>
```

В Docker Compose production database URL задается внутри `docker-compose.yml`, поэтому локальное значение `DATABASE_URL` из `.env` для контейнеров `web` и `bot` будет переопределено на PostgreSQL.

### 6. Поднять контейнеры
Для webhook-режима поднимайте только `db` и `web`:
```bash
docker compose config -q
docker compose up -d --build db web
docker compose run --rm web alembic upgrade head
docker compose ps
```

Проверка локально на сервере:
```bash
curl -fsS http://127.0.0.1:18080/health
curl -fsS http://127.0.0.1:18080/ready
```

### 7. Настроить HTTPS reverse proxy
Создайте nginx site:
```bash
nano /etc/nginx/sites-available/vpn-seller
```

Конфиг:
```nginx
server {
    listen 80;
    server_name bot.super-lemming.online;

    location / {
        proxy_pass http://127.0.0.1:18080;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Включите site и получите TLS-сертификат:
```bash
ln -s /etc/nginx/sites-available/vpn-seller /etc/nginx/sites-enabled/vpn-seller
nginx -t
systemctl reload nginx
certbot --nginx -d bot.super-lemming.online
```

Проверка снаружи:
```bash
curl -fsS https://bot.super-lemming.online/health
curl -fsS https://bot.super-lemming.online/ready
```

### 8. Подключить Telegram webhook
На сервере:
```bash
set -a
. ./.env
set +a
WEBHOOK_URL="$APP_BASE_URL/telegram/webhook/$BOT_WEBHOOK_SECRET"
curl -fsS -X POST "https://api.telegram.org/bot$BOT_TOKEN/setWebhook" -d "url=$WEBHOOK_URL"
curl -fsS "https://api.telegram.org/bot$BOT_TOKEN/getWebhookInfo"
```

Telegram webhook URL:
```text
https://bot.super-lemming.online/telegram/webhook/<BOT_WEBHOOK_SECRET>
```

Не запускайте сервис `bot` в webhook-режиме для того же `BOT_TOKEN`.

### 9. Подключить VK callback
В настройках сообщества VK:
```text
Callback URL:
https://bot.super-lemming.online/vk/callback
```

В `.env` должны быть заполнены:
```env
VK_GROUP_ID=<vk-group-id>
VK_GROUP_TOKEN=<vk-group-token>
VK_CONFIRMATION_TOKEN=<vk-confirmation-token>
VK_CALLBACK_SECRET=<vk-callback-secret>
```

Включите события сообщений в Callback API. После сохранения VK отправит confirmation request, а приложение должно вернуть `VK_CONFIRMATION_TOKEN`.

### 10. Подключить WhatsApp webhook
В Meta for Developers укажите:
```text
Callback URL:
https://bot.super-lemming.online/whatsapp/webhook

Verify token:
значение WHATSAPP_VERIFY_TOKEN из .env
```

Подпишитесь на webhook field:
```text
messages
```

В `.env` должны быть заполнены:
```env
WHATSAPP_PHONE_NUMBER_ID=<whatsapp-phone-number-id>
WHATSAPP_ACCESS_TOKEN=<whatsapp-access-token>
WHATSAPP_VERIFY_TOKEN=<whatsapp-verify-token>
WHATSAPP_APP_SECRET=<whatsapp-app-secret>
```

### 11. Финальная проверка всех контуров
Telegram:
- открыть бота и выполнить `/start`;
- выполнить `/admin_settings donate_url <your-donate-stream-url>`;
- выполнить `/admin_settings support_username <support-telegram-username>` или `/admin_settings support_url <support-url>`;
- открыть каталог, создать заказ, вручную подтвердить оплату, проверить выдачу и PDF.

VK:
- написать сообщение в сообщество;
- пройти каталог и оформление заказа;
- проверить, что заказ виден через Telegram-админку;
- после подтверждения оплаты проверить доставку в VK.

WhatsApp:
- написать на WhatsApp Business номер;
- пройти каталог и оформление заказа;
- проверить доставку после подтверждения оплаты.

Общие проверки:
```bash
docker compose logs -f web
curl -fsS https://bot.super-lemming.online/health
curl -fsS https://bot.super-lemming.online/ready
```

### 12. Обновление после новых push
```bash
cd /opt/vpn-seller
git pull --ff-only
docker compose up -d --build db web
docker compose run --rm web alembic upgrade head
docker compose ps
```

## База данных и миграции
Модели находятся в `app/db/models.py`, миграции - в `app/db/migrations/versions`.

Таблица `shop_settings` хранит редактируемые из админки реквизиты магазина: Donate.Stream URL и контакты поддержки. Для локального старта используются env fallback значения, но рабочие реквизиты должны задаваться через `/admin_settings`.

Локальная SQLite БД:
```text
data/app.db
```

Проверка миграций на чистой временной SQLite БД:
```powershell
$db = Join-Path $env:TEMP "vpn_seller_migration_check.db"
$env:DATABASE_URL = "sqlite+aiosqlite:///" + (($db -replace "\\","/"))
python -m alembic upgrade head
Remove-Item -LiteralPath $db -Force
```

Seed тарифов:
```powershell
python scripts/seed_plans.py
```

На старте приложение может применять цены из `content/pricing.json`, если `APPLY_PLAN_PRICING_ON_STARTUP=true`.

## Каналы: Telegram, VK, WhatsApp
Telegram:
- основной пользовательский бот;
- вся админка;
- ручное подтверждение Donate.Stream оплат;
- импорт, экспорт, Hiddify, поиск заказов, промокоды.

VK:
- пользовательский каталог;
- покупка;
- мои покупки;
- поддержка;
- доставка доступа в VK-диалог, если заказ создан из VK.

WhatsApp:
- пользовательский каталог;
- покупка через кнопки/списки или текстовый fallback;
- мои покупки;
- поддержка;
- доставка доступа и ссылки на PDF-путеводитель.

VK и WhatsApp не имеют отдельной бизнес-логики оплаты или выдачи: они используют общие services.

## Платежи
Поддержанные провайдеры:
- `fake` - локальная разработка и тесты.
- `donate_stream` - ручная проверка оплаты администратором.

Donate.Stream flow:
1. Пользователь выбирает тариф и режим выдачи.
2. Бот проверяет доступность.
3. Бот создает заказ.
4. Для локального склада резервируется один ключ.
5. Бот создает платеж и отправляет ссылку Donate.Stream.
6. Админ получает review card в Telegram.
7. Админ вручную проверяет донат.
8. Админ подтверждает оплату в Telegram.
9. Сервис помечает payment как `succeeded`.
10. Запускается выдача.
11. Доставка идет через `delivery_jobs`.

Donate.Stream URL берется из `shop_settings`, если он задан через `/admin_settings donate_url ...`; иначе используется fallback `DONATE_STREAM_URL` из `.env`.

Webhook-capable providers должны проходить через общий интерфейс:
- `create_payment(...)`
- `parse_webhook(...)`
- `get_payment_status(...)`

Webhook-события сначала сохраняются в `payment_events`, затем обрабатываются. Повторное событие становится безопасным duplicate/no-op.

Сравнение суммы платежа выполняется числово через `Decimal`, поэтому эквивалентные значения вроде `299` и `299.00` считаются равными.

## Выдача доступов
Статусы заказов:
- `created`
- `pending_payment`
- `paid`
- `issued`
- `paid_but_not_issued`
- `canceled`
- `refunded`
- `payment_failed`
- `expired_reservation`

Статусы ключей:
- `available`
- `reserved`
- `issued`
- `disabled`
- `broken`
- `archived`

Для локальных ключей:
- при создании заказа ключ резервируется;
- TTL резерва по умолчанию `15` минут;
- просроченный резерв снимается только если заказ все еще неоплачен;
- выдача происходит в транзакции;
- после коммита создается delivery job.

Для Hiddify:
- деньги не принимаются, если тариф не может быть выдан;
- доступ генерируется после оплаты;
- результат сохраняется как выданный `vpn_keys` record для аудита, экспорта и повторной доставки.

Если выдача не удалась после оплаты, заказ переводится в `paid_but_not_issued`, администраторы получают alert, а reconciliation может безопасно повторить выдачу.

## Hiddify Manager API v2
Подключение панели:
1. Откройте `/admin_hiddify`.
2. Выберите добавление сервера.
3. Укажите:
   - display name;
   - country name;
   - panel base URL;
   - admin proxy path;
   - client proxy path;
   - `Hiddify-API-Key`.
4. Бот проверит:
   - `GET /{admin_proxy_path}/api/v2/panel/ping/`
   - `GET /{admin_proxy_path}/api/v2/panel/info/`
5. Если проверка успешна, сервер сохраняется encrypted-at-rest.

Hiddify-серверы хранят:
- название;
- страну;
- base URL;
- admin/client proxy paths;
- зашифрованный API key;
- активность;
- health status;
- последнюю ошибку;
- время последнего использования.

Админка позволяет:
- посмотреть список серверов;
- включить/выключить сервер;
- проверить health;
- посмотреть карточку нагрузки;
- собрать usage snapshot вручную;
- импортировать серверы из XLSX.

## MTProxy и superkey
MTProxy:
- тарифы имеют `provisioning_mode=mtproxy`;
- локальный ключ не резервируется;
- после оплаты бот измеряет нагрузку активных серверов;
- выбирает сервер с минимальным числом активных пользователей;
- создает пользователя в Hiddify;
- доставляет MTProxy/MTProto ссылки;
- выбранный сервер сохраняется в заказе для аудита.

Superkey:
- требует минимум два активных сервера;
- создает доступы по активным серверам;
- пользователь получает один subscription URL;
- backend endpoint `/subscriptions/{token}` собирает и дедуплицирует underlying subscriptions;
- частичный сбой сборки не считается успешной выдачей.

## Промокоды и цены
Цены редактируются в:
```text
content/pricing.json
```

Тексты редактируются в:
```text
content/messages.json
```

Промокоды поддерживают:
- процентную скидку;
- фиксированную скидку;
- лимит использований;
- запрет повторного использования одним пользователем;
- сохранение скидки и итоговой суммы в заказе;
- audit log.

Команды:
```text
/promo CODE
/admin_promo
/admin_promo CODE percent 10 [max_uses]
/admin_promo CODE fixed 100 [max_uses]
```

Скидка не опускает сумму ниже `MIN_ORDER_AMOUNT`.

## Админка
Telegram admin commands:
```text
/admin_import
/admin_export
/admin_export [all|available|issued]
/admin_export orders
/admin_stock
/admin_hiddify
/admin_order <order_id|payment_id|telegram_user_id|vk_user_id|whatsapp_phone|username>
/admin_settings
/admin_settings donate_url https://...
/admin_settings support_username username
/admin_settings support_url https://...
/admin_notify @username|telegram_user_id|vk_user_id|whatsapp_phone text
/admin_broadcast text
/admin_promo
/admin_promo CODE percent 10 [max_uses]
/admin_promo CODE fixed 100 [max_uses]
```

Админские действия:
- ручное подтверждение оплаты;
- отмена неоплаченного заказа;
- повторная отправка выданного доступа;
- replacement key/access;
- refund marker;
- поиск заказов;
- просмотр audit events;
- просмотр проблемных заказов через статусы;
- импорт/экспорт XLSX;
- управление Hiddify-серверами;
- настройка реквизитов магазина: Donate.Stream URL и контакты поддержки;
- точечные уведомления пользователю и broadcast по клиентской базе.

Replacement:
- локальный ключ: старый ключ помечается `broken`, новый берется со склада;
- Hiddify server access: создается новый remote access;
- MTProxy: выбирается least-loaded сервер, по возможности не предыдущий;
- superkey: пересобирается по текущим активным серверам;
- delivery всегда идет через `delivery_jobs`.

## XLSX импорт и экспорт
Assets:
- `assets/admin_keys_template.xlsx`
- `assets/hiddify_servers_template.xlsx`
- `assets/sample_inventory_export.xlsx`
- `assets/МОЙ-ПУТЕВОДИТЕЛЬ.pdf`

Импорт ключей:
- sheet: `keys`
- required columns:
  - `plan_code`
  - `key_value`
- optional columns:
  - `external_ref`
  - `comment`
  - `expires_at`

Импорт Hiddify servers:
- sheet: `servers`
- columns:
  - `name`
  - `country_name`
  - `base_url`
  - `admin_proxy_path`
  - `client_proxy_path`
  - `api_key`
  - `is_active`

Заполненные XLSX с ключами или Hiddify API keys считаются секретными файлами.

## Webhook endpoints
Telegram:
```text
POST /telegram/webhook/{BOT_WEBHOOK_SECRET}
```

Пример:
```text
https://example.com/telegram/webhook/telegram-webhook-secret
```

VK:
```text
POST /vk/callback
```

WhatsApp:
```text
GET  /whatsapp/webhook
POST /whatsapp/webhook
```

Файлы:
```text
GET /files/setup-guide
```

Subscriptions:
```text
GET /subscriptions/{token}
```

Production webhook рекомендации:
- ставьте приложение за HTTPS reverse proxy;
- держите webhook secrets только в env;
- не запускайте polling и webhook одновременно для одного Telegram token;
- не подтверждайте оплату redirect/callback-ом пользователя;
- ключи доставляются только после server-side confirmation/admin confirmation.

Telegram webhook setup:
```powershell
$env:BOT_TOKEN = "<prod-bot-token>"
$env:APP_BASE_URL = "https://vpn.example.com"
$env:BOT_WEBHOOK_SECRET = "<prod-webhook-secret>"
$webhookUrl = "$env:APP_BASE_URL/telegram/webhook/$env:BOT_WEBHOOK_SECRET"
Invoke-RestMethod "https://api.telegram.org/bot$env:BOT_TOKEN/setWebhook" -Method Post -Body @{url=$webhookUrl}
Invoke-RestMethod "https://api.telegram.org/bot$env:BOT_TOKEN/getWebhookInfo"
```

Back to polling:
```powershell
Invoke-RestMethod "https://api.telegram.org/bot$env:BOT_TOKEN/deleteWebhook"
docker compose up -d bot
```

## Health endpoints
```text
GET /health
GET /ready
```

Локальная проверка:
```powershell
Invoke-RestMethod http://localhost:18080/health
Invoke-RestMethod http://localhost:18080/ready
```

## Фоновые процессы
Фоновые задачи запускаются контейнером/процессом приложения:
- cleanup просроченных резервов;
- payment reconciliation;
- delivery job retry;
- Hiddify usage snapshots;
- low stock/admin alerts;
- paid-but-not-issued recovery attempts.

Delivery jobs:
- имеют unique `dedupe_key`;
- поддерживают `pending`, `processing`, `retry`, `delivered`, `failed`;
- блокируются через `FOR UPDATE SKIP LOCKED`, где поддерживается БД;
- не откатывают сам заказ при ошибке доставки.

## Безопасность
- `ENCRYPTION_KEY` обязателен для защиты ключей и API secrets.
- VPN keys хранятся encrypted-at-rest.
- Hiddify API keys хранятся encrypted-at-rest.
- Для дедупликации используется fingerprint, а не открытый ключ.
- Полные ключи и API secrets не должны логироваться.
- `audit_logs` append-only на уровне приложения.
- Пользовательские каналы VK/WhatsApp не подтверждают оплату.
- Админские destructive actions требуют явного действия администратора.

## Runbook по контурам
### Local
Назначение: быстрая проверка пользовательского сценария, админки, manual-flow оплаты и выдачи без внешней инфраструктуры.

1. Подготовьте `.env`:
```powershell
Copy-Item .env.example .env
```

2. Минимальные значения:
```env
APP_MODE=polling
APP_BASE_URL=http://localhost:8000
DATABASE_URL=sqlite+aiosqlite:///./data/app.db
PAYMENT_PROVIDER=fake
BOT_TOKEN=<local-bot-token>
ADMIN_IDS=<your-telegram-id>
```

3. Установите зависимости:
```powershell
python -m pip install -e ".[dev]"
```

4. Проверьте миграции на временной БД.

5. Запустите polling:
```powershell
python -m app.main --polling
```

6. Smoke-test Telegram:
- `/start`;
- `/admin_settings donate_url https://...`;
- `/admin_settings support_username username` или `/admin_settings support_url https://...`;
- открыть каталог;
- создать заказ;
- подтвердить оплату в админском сообщении;
- убедиться, что пользователь получил доступ и PDF;
- открыть `/admin_hiddify` и карточку нагрузки.

7. Для health/subscription endpoints запустите web:
```powershell
python -m app.main
```

8. Для VK/WhatsApp local smoke нужен публичный HTTPS tunnel к `localhost:8000`, затем временно поставить webhook URLs:
```text
${APP_BASE_URL}/vk/callback
${APP_BASE_URL}/whatsapp/webhook
```

### Staging
Назначение: проверить Docker, PostgreSQL, реальные Hiddify-панели и админские операции на отдельном тестовом боте.

Минимальные env:
```env
APP_ENV=staging
APP_MODE=polling
APP_BIND_PORT=18080
APP_BASE_URL=https://staging.example.com
PAYMENT_PROVIDER=fake
BOT_TOKEN=<staging-bot-token>
ADMIN_IDS=<your-telegram-id>
```

Запуск:
```powershell
docker compose config -q
docker compose up -d --build db web bot
docker compose run --rm web alembic upgrade head
```

Проверить:
- `/health` и `/ready`;
- `/start`;
- `/admin_hiddify`;
- подключение Hiddify вручную и через XLSX;
- healthcheck сервера;
- карточку нагрузки;
- manual usage snapshot;
- MTProxy выдачу на least-loaded сервер;
- superkey выдачу;
- VK callback;
- WhatsApp webhook;
- delivery в тот канал, где создан заказ.

### Production
Назначение: боевой запуск с реальными платежами, доменом, Hiddify и admin alerts.

Минимальные env:
```env
APP_ENV=production
APP_MODE=web
APP_BIND_PORT=18080
APP_BASE_URL=https://vpn.example.com
PAYMENT_PROVIDER=donate_stream
DONATE_STREAM_URL=https://lk.donate.stream/
BOT_TOKEN=<prod-bot-token>
ADMIN_IDS=<admin-telegram-id-list>
```

After production startup, set real shop requisites from Telegram admin:
```text
/admin_settings donate_url <your-donate-stream-url>
/admin_settings support_username <support-telegram-username>
```

Запуск webhook mode:
```powershell
docker compose up -d --build db web
docker compose run --rm web alembic upgrade head
```

Настроить:
- Telegram webhook;
- VK Callback API URL: `https://vpn.example.com/vk/callback`;
- WhatsApp webhook URL: `https://vpn.example.com/whatsapp/webhook`;
- HTTPS reverse proxy на `web:8000` или host port `18080`.

Боевой smoke-test:
- `/start`;
- задать реальные реквизиты через `/admin_settings donate_url <your-donate-stream-url>`;
- задать поддержку через `/admin_settings support_username <support-telegram-username>` или `/admin_settings support_url <support-url>`;
- `/admin_hiddify`;
- подключить реальные Hiddify-панели;
- проверить каждую панель;
- собрать usage snapshot;
- сделать тестовую покупку короткого тарифа;
- вручную проверить Donate.Stream;
- подтвердить оплату в Telegram-админке;
- убедиться, что доступ доставлен только после подтверждения;
- открыть `/admin_order <order_id>`;
- проверить статус, delivery и audit events;
- проверить VK и WhatsApp пользовательские сценарии, если каналы включены.

После запуска:
- следить, что snapshots собираются каждый час;
- после добавления Hiddify-панели собирать первый snapshot вручную;
- проверять `paid_but_not_issued`;
- держать `HIDDIFY_ALERT_COOLDOWN_MINUTES=1440`, чтобы alerts не спамили.

## Проверки и тесты
Минимальная проверка Python:
```powershell
python -m compileall app tests
```

Полный тестовый набор:
```powershell
python -m pytest -q
```

JSON:
```powershell
python -m json.tool content/messages.json
python -m json.tool content/pricing.json
```

Docker:
```powershell
docker compose config -q
```

PostgreSQL concurrency test:
```powershell
$env:TEST_POSTGRES_DSN="postgresql+asyncpg://user:pass@host:5432/dbname"
python -m pytest -q tests/test_payments.py
```

Reproducible PostgreSQL validation in Docker:
```powershell
docker compose up -d db
docker run --rm --network vpn-seller_default -v "${PWD}:/workspace" -w /workspace python:3.11-slim sh -lc "pip install -q --upgrade pip && pip install -q -e '.[dev]' && TEST_POSTGRES_DSN='postgresql+asyncpg://vpn_seller:vpn_seller@db:5432/vpn_seller' python -m pytest -q"
```

Последняя проверка в этом workspace:
```text
python -m compileall app tests
python -m pytest -q
docker compose config -q
python -m json.tool content/messages.json
python -m json.tool content/pricing.json
alembic upgrade head на чистой временной SQLite БД
```

Результат после последнего рефакторинга:
```text
46 passed, 1 skipped
```

Пропущенный тест требует `TEST_POSTGRES_DSN`.

## Чистота репозитория
Проект написан достаточно чисто по слоям: handlers тонкие, бизнес-логика в services, доступ к БД в repositories. Основные доменные инварианты платежей/выдачи прикрыты тестами.

Что было убрано:
- `docs/` - содержимое перенесено в этот README.
- `vpn_seller.egg-info/` - производный build/install artifact, важные зависимости перенесены в `requirements.txt` и `requirements-dev.txt`.

Что желательно держать вне git/релиза:
- `.pytest_cache/`
- `__pycache__/`
- `*.pyc`
- `vpn_seller.egg-info/`
- локальные БД в `data/*.db`
- logs в `logs/`
- реальные XLSX с ключами или API keys
- `.env`

`.gitignore` уже добавлен и покрывает эти правила. Перед релизом достаточно убедиться, что в рабочей папке нет секретных `.env`, локальных БД, cache-директорий и реальных XLSX с ключами/API secrets.
