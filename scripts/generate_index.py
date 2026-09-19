#!/usr/bin/env python3
"""Generate the portal home page from its template and source manifest."""

from __future__ import annotations

import argparse
import sys
from html import escape
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError as error:  # pragma: no cover - depends on local environment
    raise SystemExit(
        "PyYAML is required. Install it with: python -m pip install PyYAML"
    ) from error


PLACEHOLDER = "{{ source_links }}"
REPOSITORIES_PLACEHOLDER = "{{ source_repositories }}"


class IndexGenerationError(Exception):
    """A template or manifest error with an actionable message."""


def repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


def load_sources(manifest_path: Path) -> list[dict[str, Any]]:
    try:
        payload = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except OSError as error:
        raise IndexGenerationError(f"Cannot read source manifest: {error}") from error
    except yaml.YAMLError as error:
        raise IndexGenerationError(f"Invalid source manifest YAML: {error}") from error
    sources = payload.get("sources") if isinstance(payload, dict) else None
    if not isinstance(sources, list) or not sources:
        raise IndexGenerationError("Source manifest must contain a non-empty 'sources' list.")
    return sources


def source_links(sources: list[dict[str, Any]]) -> str:
    links: list[str] = []
    for position, source in enumerate(sources, start=1):
        if not isinstance(source, dict):
            raise IndexGenerationError(f"Source #{position} must be a YAML mapping.")
        missing = [
            key
            for key in ("id", "title", "description", "target_path", "entrypoint")
            if not isinstance(source.get(key), str) or not source[key]
        ]
        if missing:
            raise IndexGenerationError(
                f"Source #{position} is missing index fields: {', '.join(missing)}."
            )
        target_path = source["target_path"].replace("\\", "/").strip("/")
        entrypoint = source["entrypoint"].replace("\\", "/").lstrip("/")
        if ".." in Path(target_path).parts or ".." in Path(entrypoint).parts:
            raise IndexGenerationError(f"Source {source['id']} has an unsafe index path.")
        links.append(
            f"- [{source['title']}](generated/{target_path}/{entrypoint})  \n"
            f"  {source['description']}"
        )
    return "\n".join(links)


def source_repository_links(sources: list[dict[str, Any]]) -> str:
    links: list[str] = []
    for position, source in enumerate(sources, start=1):
        if not isinstance(source, dict):
            raise IndexGenerationError(f"Source #{position} must be a YAML mapping.")
        missing = [
            key
            for key in ("id", "title", "repository")
            if not isinstance(source.get(key), str) or not source[key]
        ]
        if missing:
            raise IndexGenerationError(
                f"Source #{position} is missing repository-link fields: {', '.join(missing)}."
            )
        links.append(
            "    - "
            f'<a href="https://github.com/{escape(source["repository"], quote=True)}" '
            f'target="_blank" rel="noopener noreferrer">{escape(source["title"])}</a>'
        )
    return "\n".join(links)


def parse_arguments() -> argparse.Namespace:
    root = repository_root()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template", type=Path, default=root / "config" / "index_template.md")
    parser.add_argument("--sources", type=Path, default=root / "config" / "sources.yml")
    parser.add_argument("--output", type=Path, default=root / "docs" / "index.md")
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    try:
        template = arguments.template.read_text(encoding="utf-8")
    except OSError as error:
        raise IndexGenerationError(f"Cannot read index template: {error}") from error
    for placeholder in (PLACEHOLDER, REPOSITORIES_PLACEHOLDER):
        if template.count(placeholder) != 1:
            raise IndexGenerationError(
                f"Index template must contain {placeholder!r} exactly once."
            )

    sources = load_sources(arguments.sources)
    content = template.replace(PLACEHOLDER, source_links(sources)).replace(
        REPOSITORIES_PLACEHOLDER, source_repository_links(sources)
    )
    output = arguments.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(content, encoding="utf-8")
    print(f"Generated {output}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except IndexGenerationError as error:
        print(f"Index generation failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
