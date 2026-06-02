# Архитектура

`vpn-seller` демонстрирует эксплуатацию небольшого commerce/bot-сервиса: Telegram polling/web runtime, PostgreSQL, миграции, health endpoint и интеграционные тесты вокруг заказов, платежей, импортов и Hiddify.

```mermaid
flowchart LR
    TG[Telegram user] --> Bot[bot container]
    Admin[Operator] --> Bot
    Webhook[External callbacks] --> Web[web container]
    Bot --> DB[(PostgreSQL)]
    Web --> DB
    Bot --> H[Hiddify API]
    Bot --> Pay[Payment provider]
```

Compose запускается без обязательного `.env`; для runtime smoke задан local-only `BOT_TOKEN` по валидному формату, но это не реальный Telegram secret. Реальные токены должны задаваться только локально или в secret manager.
