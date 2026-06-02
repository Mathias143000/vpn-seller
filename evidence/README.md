# Доказательства

Минимальный reproducible evidence:

```powershell
docker compose config --quiet
python -m compileall app tests
pytest -q
python -m pip_audit -r requirements-dev.txt
python scripts\generate_sbom.py --output artifacts\hardening\sbom.cdx.json
```

`artifacts/` не коммитится. Сгенерированный SBOM нужен для локального или CI-review.
