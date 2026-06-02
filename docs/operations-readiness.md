# Operations Readiness

- CI: compile, pytest, dependency audit, compose config, SBOM generation.
- Supply chain: GHCR image build/push on protected refs, Trivy image scan, SBOM artifact, keyless cosign sign/verify. Details: [supply-chain.md](supply-chain.md).
- Security: Trivy fs scan, secret guard, runtime artifact guard, no `latest` image guard.
- Runtime: separate `web` and `bot` containers with PostgreSQL dependency and healthcheck.
- Secrets: `.env.example` contains placeholders only; `.env` is ignored and optional for compose config. Compose default `BOT_TOKEN` is syntactically valid for local smoke only and is not a real secret.
- DR: PostgreSQL volume is explicit, but backup/restore automation is not claimed as complete.

Статус: production-like application ops lab для небольшого stateful bot/commercial сервиса.
