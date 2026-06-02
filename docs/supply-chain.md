# Supply chain and registry flow

Тип: CI/CD lab evidence. Это не доказательство реального production registry ownership.

## Что проверяет workflow

Workflow: `.github/workflows/supply-chain.yml`.

На `pull_request`:

- ставит Python dependencies;
- запускает compile/test gate;
- собирает Docker image с tag `ghcr.io/<owner>/vpn-seller:<git-sha>`;
- сканирует image через Trivy на `HIGH,CRITICAL`;
- генерирует CycloneDX-style SBOM и публикует его как CI artifact.

На `push` в `main` или `v*` tag дополнительно:

- пушит image в GHCR;
- подписывает image через keyless `cosign`;
- проверяет подпись через GitHub OIDC identity workflow.

## Команды для локальной перепроверки

```powershell
python -m compileall app tests
pytest -q
docker build --pull --tag vpn-seller:local .
trivy image --severity HIGH,CRITICAL --ignore-unfixed --exit-code 1 vpn-seller:local
python scripts\generate_sbom.py --output artifacts\supply-chain\sbom.cdx.json
```

Проверка опубликованного образа после успешного CI:

```powershell
$env:IMAGE_URI = "ghcr.io/<owner>/vpn-seller:<git-sha>"
cosign verify $env:IMAGE_URI `
  --certificate-identity-regexp "https://github.com/<owner>/<repo>/.github/workflows/supply-chain.yml@refs/.*" `
  --certificate-oidc-issuer "https://token.actions.githubusercontent.com"
```

## Release and rollback flow

Release unit: immutable image tag by Git SHA. `latest` не используется.

Rollback для этого repo-level flow:

1. выбрать предыдущий successful Git SHA из GitHub Actions или GHCR packages;
2. проверить подпись `cosign verify`;
3. запустить compose/runtime с предыдущим image tag или пересобрать из предыдущего commit;
4. прогнать `/health`, `/ready`, migration smoke и `pytest -q`.

## Доказательства

- CI workflow: `.github/workflows/supply-chain.yml`
- SBOM generator: `scripts/generate_sbom.py`
- Docker runtime: `Dockerfile`, `docker-compose.yml`
- Dependency gate: `.github/workflows/ci.yml`
- Secret/artifact/image hygiene gate: `.github/workflows/security.yml`

## Ограничения

- SBOM в этом проекте lightweight CycloneDX-style и нужен как review artifact, а не как полный SLSA provenance.
- Keyless cosign signature покрывает GHCR image, но не заменяет отдельные deployment attestations.
- Реальный production registry promotion, admission policy и emergency rollback должны жить в отдельном deployment repo или platform repo.
