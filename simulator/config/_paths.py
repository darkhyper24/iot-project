"""Shared path helpers for config loaders."""

from __future__ import annotations

from pathlib import Path


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def resolve_under_repo(path: str) -> Path:
    p = Path(path)
    if not p.is_absolute():
        p = repo_root() / p
    return p
