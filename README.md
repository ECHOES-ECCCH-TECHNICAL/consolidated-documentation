# ECHOES Technical Documentation

Consolidated documentation portal for ECHOES technical resources. It contains scripts that collect selected content from several GitHub repositories, while each original repository remains the authoritative place to propose content changes.

> [!WARNING]
> This portal is experimental. Its source selection, generated navigation and
> publication workflow may change in the future.

## Project structure

- `config/` - source manifest and build templates for Zensical and MkDocs.
- `docs/` - portal assets and generated documentation inputs. Generated files are
  ignored by Git.
- `glossary/` - source CSV for the shared glossary.
- `scripts/` - scripts for collecting content and generating the portal home page, abbreviations definitions from the glossary and final configuration.
- `.github/workflows/` - workflows for validation, preview and GitHub Pages deployment.

## Sources

[`config/sources.yml`](config/sources.yml) defines every repository to be consolidated into the portal. Each entry includes its stable `id`, display `title` and `description`, GitHub
`repository`, `branch`, source and destination paths, entry page, and file
selection rules. Optional `navigation` settings either build a hierarchy from the
source filesystem or reuse an upstream `mkdocs.yml` navigation.

Example:

```yaml
- id: example
  title: Example documentation
  repository: organisation/repository
  branch: main
  source_path: docs
  target_path: example
  entrypoint: index.md
  include:
    - "**"
```

## Main scripts

- `collect_docs.py` clones the configured sources and copies their selected files
  into `docs/generated/`. It also captures upstream navigation where configured
  in the source repository.
- `generate_glossary_abbreviations.py` converts the glossary CSV into MkDocs/Zensical
  abbreviation definitions.
- `generate_index.py` creates the portal home page from its template and source
  descriptions.
- `generate_mkdocs_config.py` generates the site navigation and final configuration
  from a selected template.

## Build locally

Production builds and deployment are handled through GitHub Actions. If you want
to build the portal locally, you can use either MkDocs or Zensical. Install the
corresponding dependencies from [`requirements-zensical.txt`](requirements-zensical.txt)
or [`requirements.txt`](requirements.txt) first.

Zensical is the preferred builder:

```powershell
python scripts/collect_docs.py
python scripts/generate_glossary_abbreviations.py
python scripts/generate_index.py
python scripts/generate_mkdocs_config.py --template config/zensical_template.yml --output zensical.yml --builder-name Zensical
zensical build --config-file zensical.yml
```

The generated site is written to `site-zensical/`. MkDocs remains available as a compatibility fallback.

## Automation

GitHub Actions regenerates the portal and deploys the Zensical build to GitHub
Pages. 

