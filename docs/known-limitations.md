# Честные Ограничения

- Это lab/runtime evidence, а не подтверждение real production ownership.
- Нет Kubernetes/GitOps сценария; этот проект намеренно остается Docker Compose application ops slice.
- Backup/restore для PostgreSQL описан как operational gap, не как закрытый DR DoD.
- GHCR/cosign flow является CI/CD lab evidence; реальный production registry promotion и deployment attestations не заявляются.
- Live payment, Telegram, VK, WhatsApp и Hiddify credentials не коммитятся и не проверяются в CI.
- Demo credentials/placeholders в `.env.example` и compose defaults предназначены только для локального запуска.
