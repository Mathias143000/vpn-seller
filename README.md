# VPN Seller Ops Lab

Компактный stateful service lab для DevOps / Platform ревью. Ценность проекта не в Telegram/VPN бизнес-логике, а в проверяемом runtime: Docker Compose, PostgreSQL, Alembic migrations, health/readiness endpoints, tests, dependency audit, Trivy, SBOM, GHCR/cosign supply-chain flow и GitLab CI mirror.

Это production-like service evidence. Реальные токены, платежные credentials и production secrets не публикуются.

## Что Проверяет Техлид

- Multi-container runtime: `web`, `bot`, `PostgreSQL`.
- `/health` и `/ready`.
- Alembic migrations на чистой БД.
- Non-root Docker image и `.dockerignore`.
- Pytest, compile checks, `pip-audit`.
- Trivy filesystem/image scan.
- SBOM generation.
- GHCR publish + keyless cosign sign/verify flow.
- GitHub Actions и GitLab CI portability example.

## Быстрый Запуск

```powershell
docker compose config --quiet
python -m compileall app tests
pytest -q
python -m pip_audit -r requirements-dev.txt
python scripts\generate_sbom.py --output artifacts\hardening\sbom.cdx.json
docker build --pull --tag vpn-seller:local .
```

Runtime smoke:

```powershell
docker compose up -d --build db web
docker compose run --rm web alembic upgrade head
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:18080/health
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:18080/ready
docker compose down -v --remove-orphans
```

## Доказательства

- [docs/architecture.md](docs/architecture.md) - runtime architecture.
- [docs/runbook.md](docs/runbook.md) - запуск, миграции, smoke, rollback boundaries.
- [docs/operations-readiness.md](docs/operations-readiness.md) - SLO/DR/capacity/ownership границы.
- [docs/supply-chain.md](docs/supply-chain.md) - GHCR, SBOM, cosign flow.
- [docs/ci/github-actions-vs-gitlab-ci.md](docs/ci/github-actions-vs-gitlab-ci.md) - CI portability.
- [docs/known-limitations.md](docs/known-limitations.md) - честные ограничения.
- [evidence/README.md](evidence/README.md) - evidence summary.

## CI

- GitHub Actions: tests, dependency audit, Trivy, SBOM/supply-chain flow.
- GitLab CI mirror: test/audit/trivy/sbom/docker build example.
- Actions должны быть SHA-pinned после публикации.

## Очистка

```powershell
docker compose down -v --remove-orphans
Remove-Item -Recurse -Force artifacts,.pytest_cache,__pycache__ -ErrorAction SilentlyContinue
```

## Что Осталось

- Запушить repo и убедиться, что public GitHub Actions green.
- Проверить GHCR image visibility и публичную `cosign verify` команду после push.
- Не коммитить реальные `.env`, Telegram/VK/WhatsApp/payment tokens или XLSX с ключами.

## Импорт типизированных ключей Golden VPN

Команда `/admin_import` принимает обычный XLSX или SQLite bundle формата `golden-vpn.typed-keys.v1`. Для каждой строки сохраняются тип `awg`, `trojan` или `hysteria` и статус `available` либо `issued`. Перед подтверждением бот показывает количество строк по типам и статусам; дубликаты и неизвестные тарифы отклоняются.

Ключи `available` попадают в продаваемый склад. Ключи `issued` сохраняются для учета уже действующих клиентов и повторно не выдаются. Административный XLSX-экспорт содержит колонку `key_type`.

Golden issuer bot и `vpn-seller` используют разные токены и имеют разные обязанности. Issuer на VPN-сервере только выпускает и выгружает ключи и сообщает администратору о TLS. `vpn-seller` остается источником клиентских привязок, принимает решение при форс-мажоре, уведомляет клиента и непосредственно доставляет замену через `/admin_emergency`.

## Желательно

- Runtime backup/restore drill для PostgreSQL именно в этом repo.
- Более глубокий canary/rollback flow на уровне image tags.
- OpenTelemetry tracing, если сервис станет отдельным observability кейсом.

## Честные Ограничения

- Это compact service lab, не доказательство production on-call.
- Payment/VPN integrations в публичном repo работают как local/demo paths без реальных secrets.
- Supply-chain flow становится публично проверяемым только после GHCR publish и green Actions.
