"""Lockfile and UV tooling contract. Prevent a regression to legacy installs."""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
API = ROOT / "api"
RUNTIME = {
    "fastapi", "uvicorn", "psycopg", "redis",
    "httpx", "pillow", "clerk-backend-api",
}
DEV = {"pytest", "ruff"}
LEGACY_COMMAND = re.compile(
    r"\b(?:python(?:3(?:\.\d+)?)?\s+-m\s+pip"
    r"|pip(?:3)?\s+(?:install|wheel)"
    r"|python(?:3(?:\.\d+)?)?\s+-m\s+venv)\b",
    re.IGNORECASE,
)


def _names(specifiers):
    return {
        re.split(r"[<=>!~ ]", dep, maxsplit=1)[0].split("[", maxsplit=1)[0].lower()
        for dep in specifiers
    }


def test_python_project_and_lock_cover_all_declared_dependencies():
    project = tomllib.loads((API / "pyproject.toml").read_text(encoding="utf-8"))
    lock = tomllib.loads((API / "uv.lock").read_text(encoding="utf-8"))
    assert project["project"]["requires-python"] == lock["requires-python"]
    assert (API / ".python-version").read_text(encoding="utf-8").strip() == "3.12"
    assert project["build-system"]["build-backend"] == "setuptools.build_meta"
    assert _names(project["project"]["dependencies"]) == RUNTIME
    assert _names(project["dependency-groups"]["dev"]) == DEV
    packages = {p["name"]: p for p in lock["package"]}
    assert RUNTIME | DEV <= packages.keys()
    entry = packages["locomission-api"]
    assert entry["source"] == {"editable": "."}
    assert {x["name"] for x in entry["dependencies"]} == RUNTIME
    assert {x["name"] for x in entry["dev-dependencies"]["dev"]} == DEV


def test_all_python_build_and_deployment_commands_are_uv_only():
    checked = (
        "Dockerfile", ".github/workflows/api.yml",
        "README.md", "docs/CLERK-SETUP.md",
        "docs/DEPLOY-RENDER-PAGES.md", "docs/EDITORIAL.md",
        "docs/PILOT-RELEASE.md", "docs/PRIVACY-DRAFT.md",
        "docs/RESILIENCE.md", "api/load/benchmark.py",
    )
    for name in checked:
        code = (ROOT / name).read_text(encoding="utf-8")
        assert not LEGACY_COMMAND.search(code), f"Legacy Python installer in {name}"


def test_production_docker_and_ci_require_exact_uv_lock():
    docker = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    ci = (ROOT / ".github/workflows/api.yml").read_text(encoding="utf-8")
    assert "api/uv.lock" in docker
    assert "uv sync --locked --no-dev --no-editable" in docker
    assert "/app/.venv" in docker
    assert "uv sync --locked" in ci
    assert "uv lock --check" in ci
    assert "uv run --locked" in ci
    assert "windows-uv:" in ci
    assert "python -m venv" not in docker
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "**/.venv.*" in dockerignore
    assert ".venv.*/" in gitignore
