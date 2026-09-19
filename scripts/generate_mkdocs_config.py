#!/usr/bin/env python3
"""Generate a documentation-site configuration from its template and source manifest."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError as error:  # pragma: no cover - depends on local environment
    raise SystemExit(
        "PyYAML is required. Install it with: python -m pip install PyYAML"
    ) from error


class ConfigurationError(Exception):
    """A manifest or template error with an actionable message."""


MERMAID_FENCE_FORMAT_TAG = (
    "tag:yaml.org,2002:python/name:pymdownx.superfences.fence_code_format"
)


class MermaidFenceFormat:
    """A safe placeholder for MkDocs' Mermaid fence formatter tag."""


class TemplateLoader(yaml.SafeLoader):
    """Safe YAML loader with support for the one approved MkDocs YAML tag."""


class TemplateDumper(yaml.SafeDumper):
    """Safe YAML dumper that preserves the approved MkDocs YAML tag."""


def construct_mermaid_fence_format(
    loader: TemplateLoader, node: yaml.Node
) -> MermaidFenceFormat:
    loader.construct_scalar(node)
    return MermaidFenceFormat()


def represent_mermaid_fence_format(
    dumper: TemplateDumper, value: MermaidFenceFormat
) -> yaml.Node:
    return dumper.represent_scalar(MERMAID_FENCE_FORMAT_TAG, "")


TemplateLoader.add_constructor(MERMAID_FENCE_FORMAT_TAG, construct_mermaid_fence_format)
TemplateDumper.add_representer(MermaidFenceFormat, represent_mermaid_fence_format)


def repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


def load_yaml(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = yaml.load(path.read_text(encoding="utf-8"), Loader=TemplateLoader)
    except OSError as error:
        raise ConfigurationError(f"Cannot read {label} {path}: {error}") from error
    except yaml.YAMLError as error:
        raise ConfigurationError(f"Invalid YAML in {label} {path}: {error}") from error
    if not isinstance(payload, dict):
        raise ConfigurationError(f"{label.capitalize()} must be a YAML mapping: {path}")
    return payload


def normalise_relative_path(value: str, source_id: str) -> str:
    path = value.replace("\\", "/").lstrip("/")
    if not path or ".." in Path(path).parts:
        raise ConfigurationError(f"Source {source_id} has an unsafe navigation path: {value}")
    return path


def display_name(path: Path) -> str:
    """Use the first H1 when practical, otherwise make a readable filename."""
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("# "):
                return line[2:].strip()
    except UnicodeDecodeError:
        pass
    return path.stem.replace("-", " ").replace("_", " ").title()


def directory_display_name(directory: Path) -> str:
    name = directory.name.replace("-", " ").replace("_", " ")
    return name if name.isupper() else name.title()


def filesystem_navigation(
    source: dict[str, Any], source_navigation: list[dict[str, Any]], document_root: Path, seen_paths: set[str]
) -> None:
    navigation_config = source["navigation"]
    root_value = navigation_config.get("root", "")
    root_relative = normalise_relative_path(root_value, source["id"]) if root_value else ""
    root = document_root / root_relative
    if not root.is_dir():
        raise ConfigurationError(f"Filesystem navigation root does not exist for {source['id']}: {root}")

    target_prefix = f"generated/{source['target_path'].strip('/')}/"

    def add_directory(directory: Path) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        index = directory / "index.md"
        if index.is_file():
            relative = index.relative_to(document_root).as_posix()
            document = target_prefix + relative
            if document not in seen_paths:
                items.append({"Overview": document})
                seen_paths.add(document)
        for page in sorted(directory.glob("*.md"), key=lambda item: item.name.lower()):
            if page.name.lower() == "index.md":
                continue
            relative = page.relative_to(document_root).as_posix()
            document = target_prefix + relative
            if document not in seen_paths:
                items.append({display_name(page): document})
                seen_paths.add(document)
        for child in sorted((item for item in directory.iterdir() if item.is_dir()), key=lambda item: item.name.lower()):
            child_items = add_directory(child)
            if child_items:
                items.append({directory_display_name(child): child_items})
        return items

    source_navigation.extend(add_directory(root))


def source_mkdocs_navigation(
    source: dict[str, Any], source_navigation: list[dict[str, Any]], navigation_root: Path, seen_paths: set[str]
) -> None:
    snapshot = navigation_root / f"{source['id']}.yml"
    payload = load_yaml(snapshot, f"source navigation for {source['id']}")
    upstream_navigation = payload.get("nav")
    if not isinstance(upstream_navigation, list):
        raise ConfigurationError(f"Source navigation for {source['id']} has no nav list.")
    prefix = f"generated/{source['target_path'].strip('/')}/"

    def transform(value: Any, top_level: bool = False) -> Any:
        if isinstance(value, str):
            relative = normalise_relative_path(value, source["id"])
            document = prefix + relative
            seen_paths.add(document)
            return document
        if isinstance(value, list):
            return [transform(item) for item in value]
        if isinstance(value, dict) and len(value) == 1:
            title, child = next(iter(value.items()))
            if not isinstance(title, str):
                raise ConfigurationError(f"Source navigation for {source['id']} has a non-string title.")
            if top_level and title == "Home":
                title = "Overview"
            return {title: transform(child)}
        raise ConfigurationError(f"Source navigation for {source['id']} contains an invalid item.")

    source_navigation.extend(transform(item, top_level=True) for item in upstream_navigation)


def build_navigation(sources: list[Any], docs_root: Path, navigation_root: Path) -> list[dict[str, Any]]:
    navigation: list[dict[str, Any]] = [{"Home": "index.md"}]
    seen_paths: set[str] = {"index.md"}
    for position, source in enumerate(sources, start=1):
        if not isinstance(source, dict):
            raise ConfigurationError(f"Source #{position} must be a YAML mapping.")
        missing = [
            key
            for key in ("id", "title", "target_path", "entrypoint")
            if not isinstance(source.get(key), str) or not source[key]
        ]
        if missing:
            raise ConfigurationError(
                f"Source #{position} is missing navigation fields: {', '.join(missing)}."
            )
        entrypoint = normalise_relative_path(source["entrypoint"], source["id"])
        target_path = normalise_relative_path(source["target_path"], source["id"])
        if ".." in Path(target_path).parts:
            raise ConfigurationError(f"Source {source['id']} has an unsafe navigation path.")
        document_path = f"generated/{target_path}/{entrypoint}"
        if document_path in seen_paths:
            raise ConfigurationError(f"Duplicate navigation entry point: {document_path}")
        seen_paths.add(document_path)
        static_pages = source.get("static_pages", [])
        if not isinstance(static_pages, list):
            raise ConfigurationError(f"Source {source['id']} has an invalid static_pages list.")

        source_navigation: list[dict[str, str]] = [{"Overview": document_path}]
        navigation_config = source.get("navigation")
        if navigation_config is not None:
            if not isinstance(navigation_config, dict):
                raise ConfigurationError(f"Source {source['id']} has an invalid navigation mapping.")
            strategy = navigation_config.get("strategy")
            if strategy == "filesystem":
                filesystem_navigation(
                    source, source_navigation, docs_root / "generated" / target_path, seen_paths
                )
            elif strategy == "source_mkdocs":
                # The upstream navigation already contains its Home/index page.
                source_navigation.clear()
                source_mkdocs_navigation(source, source_navigation, navigation_root, seen_paths)
            else:
                raise ConfigurationError(f"Source {source['id']} has an unsupported navigation strategy.")
        for static_position, static_page in enumerate(static_pages, start=1):
            if not isinstance(static_page, dict):
                raise ConfigurationError(
                    f"Static page #{static_position} for {source['id']} must be a YAML mapping."
                )
            static_missing = [
                key
                for key in ("title", "path")
                if not isinstance(static_page.get(key), str) or not static_page[key]
            ]
            if static_missing:
                raise ConfigurationError(
                    f"Static page #{static_position} for {source['id']} is missing: "
                    f"{', '.join(static_missing)}."
                )
            static_path = static_page["path"].replace("\\", "/").lstrip("/")
            if ".." in Path(static_path).parts:
                raise ConfigurationError(
                    f"Static page #{static_position} for {source['id']} has an unsafe path."
                )
            static_document_path = f"generated/{target_path}/{static_path}"
            if static_document_path in seen_paths:
                raise ConfigurationError(f"Duplicate navigation entry point: {static_document_path}")
            source_navigation.append({static_page["title"]: static_document_path})
            seen_paths.add(static_document_path)

        navigation.append(
            {source["title"]: source_navigation}
            if static_pages or navigation_config is not None
            else {source["title"]: document_path}
        )
    return navigation


def parse_arguments() -> argparse.Namespace:
    root = repository_root()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template", type=Path, default=root / "config" / "mkdocs_template.yml")
    parser.add_argument("--sources", type=Path, default=root / "config" / "sources.yml")
    parser.add_argument("--output", type=Path, default=root / "mkdocs.yml")
    parser.add_argument(
        "--navigation-input", type=Path, default=root / "build" / "source-navigation"
    )
    parser.add_argument(
        "--builder-name",
        default="MkDocs",
        help="Name written in validation messages and the generated-file header.",
    )
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    template = load_yaml(arguments.template, f"{arguments.builder_name} template")
    if "nav" in template:
        raise ConfigurationError(
            f"The {arguments.builder_name} template must not define 'nav'; it is generated."
        )
    manifest = load_yaml(arguments.sources, "source manifest")
    sources = manifest.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ConfigurationError("Source manifest must contain a non-empty 'sources' list.")

    template["nav"] = build_navigation(
        sources, repository_root() / "docs", arguments.navigation_input.resolve()
    )
    output = arguments.output.resolve()
    output.write_text(
        f"# Generated by scripts/generate_mkdocs_config.py for {arguments.builder_name}; do not edit directly.\n"
        + yaml.dump(template, Dumper=TemplateDumper, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    print(f"Generated {output}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ConfigurationError as error:
        print(f"Configuration generation failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
