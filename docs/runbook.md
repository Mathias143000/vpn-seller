# Операционный Runbook

## Static validation

```powershell
docker compose config --quiet
python -m compileall app tests
pytest -q
python -m pip_audit -r requirements-dev.txt
python scripts\generate_sbom.py --output artifacts\hardening\sbom.cdx.json
```

## Runtime smoke

```powershell
docker compose up -d --build db web
docker compose run --rm web alembic upgrade head
curl.exe http://127.0.0.1:18080/health
curl.exe http://127.0.0.1:18080/ready
docker compose down -v --remove-orphans
```

## Incident notes

- Если web health не отвечает, сначала проверить `docker compose ps`, затем `docker compose logs web db --no-color`.
- Если миграции/DB падают, проверить `DATABASE_URL`, состояние `db` healthcheck и логи Alembic.
- Если bot не стартует, проверить `BOT_TOKEN`, `ADMIN_IDS` и сетевую доступность Telegram API.
