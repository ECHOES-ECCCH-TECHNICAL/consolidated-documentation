#!/usr/bin/env python3
"""Collect documentation sources into one generated tree.

The script preserves each selected source subtree.  Markdown links
are not rewritten; that keeps the first aggregation pass auditable
and avoids changing original source content.
"""

from __future__ import annotations

import argparse
import fnmatch
from html import escape
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError as error:  # pragma: no cover - depends on local environment
    raise SystemExit(
        "PyYAML is required. Install it with: python -m pip install PyYAML"
    ) from error


@dataclass(frozen=True)
class Source:
    id: str
    title: str
    repository: str
    branch: str
    source_path: str
    target_path: str
    entrypoint: str
    include: tuple[str, ...]
    markdown_normalizations: tuple[str, ...]
    navigation: dict[str, Any] | None
    original_source_notice: bool


class CollectionError(Exception):
    """A configuration or collection error with an actionable message."""


def repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


def assert_within(path: Path, parent: Path, label: str) -> None:
    """Reject paths that would escape a controlled directory."""
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError as error:
        raise CollectionError(f"{label} escapes {parent}: {path}") from error


def load_sources(manifest_path: Path) -> list[Source]:
    try:
        payload = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except OSError as error:
        raise CollectionError(f"Cannot read manifest {manifest_path}: {error}") from error
    except yaml.YAMLError as error:
        raise CollectionError(f"Invalid YAML in {manifest_path}: {error}") from error

    entries = payload.get("sources") if isinstance(payload, dict) else None
    if not isinstance(entries, list) or not entries:
        raise CollectionError("Manifest must contain a non-empty 'sources' list.")

    sources: list[Source] = []
    seen_ids: set[str] = set()
    seen_targets: set[str] = set()
    required = (
        "id",
        "title",
        "repository",
        "branch",
        "source_path",
        "target_path",
        "entrypoint",
    )
    for position, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            raise CollectionError(f"Source #{position} must be a YAML mapping.")
        missing = [key for key in required if not isinstance(entry.get(key), str) or not entry[key]]
        if missing:
            raise CollectionError(f"Source #{position} is missing: {', '.join(missing)}.")

        source_id = entry["id"]
        target_path = entry["target_path"].replace("\\", "/").strip("/")
        if source_id in seen_ids:
            raise CollectionError(f"Duplicate source id: {source_id}")
        if target_path in seen_targets:
            raise CollectionError(f"Duplicate target_path: {target_path}")
        if target_path in {"", "."} or ".." in Path(target_path).parts:
            raise CollectionError(f"Invalid target_path for {source_id}: {entry['target_path']}")

        includes = entry.get("include", ["**"])
        if not isinstance(includes, list) or not includes or not all(
            isinstance(pattern, str) and pattern for pattern in includes
        ):
            raise CollectionError(f"Source {source_id} has an invalid 'include' list.")

        normalizations = entry.get("markdown_normalizations", [])
        allowed_normalizations = {
            "blank_line_before_unordered_lists",
            "four_space_nested_unordered_lists",
        }
        if not isinstance(normalizations, list) or not all(
            isinstance(item, str) and item in allowed_normalizations for item in normalizations
        ):
            raise CollectionError(
                f"Source {source_id} has unsupported markdown_normalizations."
            )

        navigation = entry.get("navigation")
        if navigation is not None:
            if not isinstance(navigation, dict):
                raise CollectionError(f"Source {source_id} has an invalid navigation mapping.")
            strategy = navigation.get("strategy")
            if strategy not in {"filesystem", "source_mkdocs"}:
                raise CollectionError(
                    f"Source {source_id} has an unsupported navigation strategy."
                )
            for key in ("root", "path"):
                if key in navigation and (
                    not isinstance(navigation[key], str) or not navigation[key]
                ):
                    raise CollectionError(f"Source {source_id} has an invalid navigation {key}.")

        original_source_notice = entry.get("original_source_notice", False)
        if not isinstance(original_source_notice, bool):
            raise CollectionError(
                f"Source {source_id} has an invalid original_source_notice value."
            )

        sources.append(
            Source(
                id=source_id,
                title=entry["title"],
                repository=entry["repository"],
                branch=entry["branch"],
                source_path=entry["source_path"],
                target_path=target_path,
                entrypoint=entry["entrypoint"].replace("\\", "/").lstrip("/"),
                include=tuple(includes),
                markdown_normalizations=tuple(normalizations),
                navigation=navigation,
                original_source_notice=original_source_notice,
            )
        )
        seen_ids.add(source_id)
        seen_targets.add(target_path)
    return sources


def run_git(arguments: list[str], cwd: Path | None = None) -> str:
    command = ["git", *arguments]
    result = subprocess.run(command, cwd=cwd, text=True, capture_output=True)
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise CollectionError(f"Git command failed ({' '.join(command)}): {detail}")
    return result.stdout.strip()


def clone_source(source: Source, checkout_root: Path) -> tuple[Path, str]:
    checkout = checkout_root / source.id
    assert_within(checkout, checkout_root, f"Checkout for {source.id}")
    if checkout.exists():
        shutil.rmtree(checkout)

    url = f"https://github.com/{source.repository}.git"
    # WP9 includes descriptive filenames that can exceed the legacy Windows
    # path limit once placed below the collector's work directory.
    run_git(
        [
            "-c",
            "core.longpaths=true",
            "clone",
            "--depth",
            "1",
            "--branch",
            source.branch,
            url,
            str(checkout),
        ]
    )
    commit = run_git(["rev-parse", "HEAD"], cwd=checkout)
    return checkout, commit


def matches_any(relative_path: Path, patterns: tuple[str, ...]) -> bool:
    portable = relative_path.as_posix()
    return any(pattern == "**" or fnmatch.fnmatchcase(portable, pattern) for pattern in patterns)


def selected_files(source_root: Path, patterns: tuple[str, ...]) -> list[Path]:
    return sorted(
        (
            item
            for item in source_root.rglob("*")
            if item.is_file() and matches_any(item.relative_to(source_root), patterns)
        ),
        key=lambda item: item.as_posix().lower(),
    )


def normalise_markdown(content: str, normalizations: tuple[str, ...]) -> str:
    """Apply explicitly configured, source-specific Markdown normalizations."""
    if "four_space_nested_unordered_lists" in normalizations:
        nested_list_pattern = re.compile(r"^( {2})([-*+]\s+)")
        content = "".join(
            nested_list_pattern.sub(r"    \2", line)
            for line in content.splitlines(keepends=True)
        )

    if "blank_line_before_unordered_lists" not in normalizations:
        return content

    result: list[str] = []
    in_fence = False
    fence_pattern = re.compile(r"^\s*(`{3,}|~{3,})")
    unordered_list_pattern = re.compile(r"^\s*[-*+]\s+")
    for line in content.splitlines(keepends=True):
        if fence_pattern.match(line):
            in_fence = not in_fence
        previous = result[-1] if result else ""
        if (
            not in_fence
            and unordered_list_pattern.match(line)
            and previous.strip()
            and not unordered_list_pattern.match(previous)
        ):
            result.append("\n")
        result.append(line)
    return "".join(result)


def original_source_notice(source: Source) -> str:
    """Create an optional, portal-level note without changing source content."""
    source_path = source.source_path.strip("/.")
    original_path = "/".join(
        part for part in (source_path, source.entrypoint.lstrip("/")) if part
    )
    url = f"https://github.com/{source.repository}/blob/{source.branch}/{original_path}"
    return (
        '!!! info "Original source"\n'
        '    This page was extracted from the '
        f'<a href="{escape(url, quote=True)}" target="_blank" rel="noopener noreferrer">'
        "original repository</a>. Some links in this document may point to files "
        "that are not included in this portal. Consult the original repository for "
        "the complete source material.\n\n"
    )


def collect_source(source: Source, checkout: Path, output_root: Path) -> int:
    source_root = (checkout / source.source_path).resolve()
    assert_within(source_root, checkout, f"source_path for {source.id}")
    if not source_root.is_dir():
        raise CollectionError(
            f"Source path does not exist for {source.id}: {source.source_path}"
        )

    destination_root = output_root / source.target_path
    assert_within(destination_root, output_root, f"target_path for {source.id}")
    files = selected_files(source_root, source.include)
    if not files:
        patterns = ", ".join(source.include)
        raise CollectionError(f"No files selected for {source.id} with: {patterns}")

    for source_file in files:
        relative_path = source_file.relative_to(source_root)
        destination = destination_root / relative_path
        assert_within(destination, output_root, f"File destination for {source.id}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source_file.suffix.lower() == ".md" and (
            source.markdown_normalizations
            or (source.original_source_notice and relative_path.as_posix() == source.entrypoint)
        ):
            try:
                content = source_file.read_text(encoding="utf-8")
            except UnicodeDecodeError as error:
                raise CollectionError(
                    f"Cannot normalize non-UTF-8 Markdown file: {source_file}"
                ) from error
            content = normalise_markdown(content, source.markdown_normalizations)
            if source.original_source_notice and relative_path.as_posix() == source.entrypoint:
                content = original_source_notice(source) + content
            destination.write_text(content, encoding="utf-8")
        else:
            shutil.copy2(source_file, destination)
    return len(files)


def collect_source_navigation(source: Source, checkout: Path, navigation_root: Path) -> None:
    """Store a safe snapshot of an upstream MkDocs navigation when requested."""
    if not source.navigation or source.navigation["strategy"] != "source_mkdocs":
        return
    config_path = checkout / source.navigation.get("path", "mkdocs.yml")
    assert_within(config_path, checkout, f"Navigation configuration for {source.id}")
    if not config_path.is_file():
        raise CollectionError(f"Navigation configuration does not exist for {source.id}: {config_path}")
    try:
        # BaseLoader is deliberate: the upstream configuration may contain MkDocs
        # Python tags, while only its plain nav structure is needed here.
        configuration = yaml.load(config_path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    except (OSError, yaml.YAMLError) as error:
        raise CollectionError(f"Cannot read navigation configuration for {source.id}: {error}") from error
    navigation = configuration.get("nav") if isinstance(configuration, dict) else None
    if not isinstance(navigation, list):
        raise CollectionError(f"Navigation configuration for {source.id} has no nav list.")
    navigation_root.mkdir(parents=True, exist_ok=True)
    (navigation_root / f"{source.id}.yml").write_text(
        yaml.safe_dump({"nav": navigation}, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )


def parse_arguments() -> argparse.Namespace:
    root = repository_root()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=root / "config" / "sources.yml")
    parser.add_argument("--output", type=Path, default=root / "docs" / "generated")
    parser.add_argument("--work-dir", type=Path, default=root / ".work" / "collect-docs")
    parser.add_argument("--report", type=Path, default=root / "build" / "collection-report.json")
    parser.add_argument(
        "--navigation-output", type=Path, default=root / "build" / "source-navigation"
    )
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    sources = load_sources(arguments.manifest.resolve())
    output_root = arguments.output.resolve()
    work_dir = arguments.work_dir.resolve()
    report_path = arguments.report.resolve()
    navigation_root = arguments.navigation_output.resolve()
    project_root = repository_root().resolve()

    for path, label in (
        (output_root, "Output"),
        (work_dir, "Work directory"),
        (report_path, "Report"),
        (navigation_root, "Navigation output"),
    ):
        assert_within(path, project_root, label)

    if output_root.exists():
        shutil.rmtree(output_root)
    if work_dir.exists():
        shutil.rmtree(work_dir)
    output_root.mkdir(parents=True)
    work_dir.mkdir(parents=True)
    if navigation_root.exists():
        shutil.rmtree(navigation_root)

    report: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sources": [],
    }
    for source in sources:
        print(f"Collecting {source.id} from {source.repository}@{source.branch}")
        checkout, commit = clone_source(source, work_dir)
        file_count = collect_source(source, checkout, output_root)
        collect_source_navigation(source, checkout, navigation_root)
        report["sources"].append(
            {
                "id": source.id,
                "title": source.title,
                "repository": source.repository,
                "branch": source.branch,
                "commit": commit,
                "source_path": source.source_path,
                "target_path": source.target_path,
                "files_imported": file_count,
            }
        )

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Collected {len(sources)} sources into {output_root}")
    print(f"Wrote report to {report_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except CollectionError as error:
        print(f"Collection failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
