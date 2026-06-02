# GitHub Actions and GitLab CI mirror

Тип: CI portability evidence. Основной CI для pinned GitHub repo остается в `.github/workflows/`.

## Зачем добавлен GitLab CI

GitLab CI часто встречается в DevOps / Platform вакансиях. `.gitlab-ci.yml` показывает, что тот же минимальный gate переносится без смены runtime assumptions:

- Python compile/test;
- dependency audit;
- SBOM artifact;
- Docker Compose config validation;
- Trivy filesystem scan;
- immutable image tag by commit SHA;
- push в GitLab Container Registry на default branch/tag.

## Что остается в GitHub Actions

| Capability | GitHub Actions | GitLab CI mirror |
|---|---|---|
| Main CI gate | yes | mirror |
| Secret/runtime artifact guard | yes | partial |
| Trivy fs scan | yes | yes |
| GHCR image push | yes | no |
| keyless cosign sign/verify | yes | no |
| SBOM artifact | yes | yes |

GitHub Actions остается source of truth для GHCR/cosign flow: [../supply-chain.md](../supply-chain.md).

## Commands to review

```powershell
docker compose config --quiet
python -m compileall app tests
pytest -q
python -m pip_audit -r requirements-dev.txt
python scripts\generate_sbom.py --output artifacts\hardening\sbom.cdx.json
```

## Ограничения

- GitLab mirror не заявляет SLSA/provenance.
- Для production GitLab flow нужно добавить protected variables, registry retention, cosign signing или notation, и policy на deploy environments.
- Нельзя считать оба CI равными источниками release truth без явного ownership решения.
