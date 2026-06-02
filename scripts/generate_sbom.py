from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def requirement_components() -> list[dict[str, str]]:
    components: list[dict[str, str]] = []
    for line in read(ROOT / "requirements.txt").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        name = re.split(r"[<>=!~]", stripped, maxsplit=1)[0].strip()
        components.append(
            {
                "type": "library",
                "name": name,
                "version": stripped[len(name) :].strip() or "range",
                "purl": f"pkg:pypi/{name}",
            }
        )
    return components


def image_components() -> list[dict[str, str]]:
    images: set[str] = set()
    for path in [ROOT / "Dockerfile", ROOT / "docker-compose.yml"]:
        text = read(path)
        for match in re.finditer(r"(?m)^FROM\s+([^\s]+)", text):
            images.add(match.group(1))
        for match in re.finditer(r"(?m)^\s*image:\s*([^\s#]+)", text):
            images.add(match.group(1))

    components: list[dict[str, str]] = []
    for image in sorted(images):
        repository, _, tag = image.rpartition(":")
        components.append(
            {
                "type": "container",
                "name": repository or image,
                "version": tag or "unpinned",
                "purl": f"pkg:docker/{repository or image}@{tag or 'unpinned'}",
            }
        )
    return components


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="artifacts/hardening/sbom.cdx.json")
    args = parser.parse_args()

    output = ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    sbom = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "version": 1,
        "metadata": {
            "component": {
                "type": "application",
                "name": "vpn-seller",
                "version": "portfolio-lab",
            }
        },
        "components": requirement_components() + image_components(),
    }
    output.write_text(json.dumps(sbom, indent=2) + "\n", encoding="utf-8")
    print(f"SBOM: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
