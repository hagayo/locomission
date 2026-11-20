# UV migration - LocoMission Python API

This repository keeps its Python application in `api/` and its Node.js
application in `mobile/`. Python 3.12 is selected by `api/.python-version`.
`api/pyproject.toml` defines **seven runtime packages and two development
packages**. `api/uv.lock` is the checked-in, resolved dependency graph.

## Why uv init is not run in the existing directory

The existing `api/pyproject.toml` already defined a Python project.
Running `uv init` over that file fails, and replacing it would discard
the established setuptools package configuration and other metadata.
The migration was prepared with `uv init --bare` in a clean scratch project,
then the original metadata was retained. All dependency declarations were
updated using `uv add` and `uv add --dev`; an actual `uv lock` resolved
their transitive versions on GitHub Actions.

There is **no need to run uv init again** in the cloned repository.

## New machine or fresh clone

Install uv using its official installation instructions, then:

```sh
cd api
uv sync --locked
uv run --locked python -m pytest -q
uv run --locked uvicorn locomission.main:app --reload
```

`uv sync --locked` selects Python 3.12, creates or updates `api/.venv`,
and verifies that `uv.lock` matches `pyproject.toml`. It never installs
the Node.js frontend. No manual activation is required.

## Existing Windows development environment: safe replacement

From **PowerShell**, close any running API process and terminals that
actively use the old environment. From the repository root:

```powershell
cd api
uv --version
uv python install 3.12
# Only if an old .venv exists and you want a clean environment:
Rename-Item .venv .venv.before-uv
uv sync --locked
uv run --locked python -m pytest -q
```

This backs up the **old** environment and creates a **new** `api/.venv`
under uv's management. If the backup name already exists, choose a new name;
do not overwrite backups. **Only after all tests pass** should you remove
the backup yourself, if desired:

```powershell
Remove-Item -Recurse -Force .venv.before-uv
```

The backup is intentionally ignored by Git. These PowerShell commands have
**not** been executed on your personal Windows computer.

## Dependency changes

Run from `api/`:

```sh
uv add "some-library>=1,<2"
uv add --dev "some-test-tool>=1,<2"
uv lock --check
uv sync --locked
```

Do not edit `uv.lock` manually. Commit both `pyproject.toml` and
`uv.lock` for dependency changes. The default dev group contains pytest
and ruff, while Docker installs `--no-dev`. The existing setuptools build
backend and the Node/npm dependency lock were not replaced.

## CI and Docker

GitHub Actions checks that the lock is current, syncs a fresh project
environment on Linux and Windows and runs tests via `uv run --locked`.
Docker uses an official version-pinned uv image **only for the build
stage**, creates `/app/.venv` from the committed lock, and copies that
environment into the original non-root Python 3.12 runtime image.
Production launches `uvicorn` from this environment with one worker.

The Windows CI job runs on GitHub-hosted Windows, not the user's PC; it
tests dependency compatibility but does not prove that a particular local
Windows installation has been migrated. PostgreSQL/Redis integration and
the Docker readiness checks run on the Linux CI job.
