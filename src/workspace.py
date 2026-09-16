"""Workspace sandbox — the trust boundary for the operator.

Every file the operator touches must resolve inside the workspace root.
Absolute paths, `..` escapes, and symlinks pointing outside are rejected
with SandboxError BEFORE any I/O happens.

The model is untrusted. The sandbox is trusted.
"""
from __future__ import annotations

import os

from src import config


class SandboxError(Exception):
    """A path tried to leave the workspace."""


def resolve_root(explicit: str | None = None) -> str:
    """Workspace root: explicit arg > MP_WORKSPACE env > repo root."""
    raw = explicit or os.environ.get("MP_WORKSPACE") or config.WORKSPACE
    root = os.path.realpath(os.path.abspath(raw))
    if not os.path.isdir(root):
        raise SandboxError(f"workspace does not exist: {raw}")
    return root


def safe_join(root: str, relpath: str) -> str:
    """Join relpath onto root, resolving symlinks, rejecting escapes.

    Returns the absolute real path. Raises SandboxError on:
      - empty path, NUL bytes
      - absolute paths (must be workspace-relative)
      - `..` segments that leave the root
      - symlinks whose target leaves the root
    """
    if not relpath or "\x00" in relpath:
        raise SandboxError("empty or NUL-containing path")
    # Normalise separators so Windows + POSIX both behave.
    rel = relpath.replace("\\", "/").strip()
    if os.path.isabs(rel) or rel.startswith("~"):
        raise SandboxError(f"absolute paths forbidden: {relpath!r}")
    joined = os.path.realpath(os.path.join(root, rel))
    if joined != root and not joined.startswith(root + os.sep):
        raise SandboxError(f"path escapes workspace: {relpath!r}")
    return joined
