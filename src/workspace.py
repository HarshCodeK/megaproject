"""The trust boundary. Every file the agent touches must land inside this.

**The model is untrusted. The sandbox is trusted.**

Why realpath first: a path like `link/../../etc/passwd` looks harmless until the
symlink is resolved. Resolving before the check means a symlink pointing outside
the root is rejected rather than followed.

The known limit, stated rather than hidden: this is a *path* sandbox, not a
*privilege* one. It stops the agent reading outside the workspace. It does not
stop a subprocess it spawns from doing so -- that needs a container, which is
what a production deployment would use.
"""
import os


class SandboxError(Exception):
    """A path tried to leave the workspace."""


def root_path() -> str:
    from src.config import ROOT
    configured = os.environ.get("MP_WORKSPACE")
    root = os.path.realpath(os.path.abspath(configured or ROOT))
    if not os.path.isdir(root):
        raise SandboxError(f"workspace does not exist: {configured or ROOT}")
    return root


def _looks_absolute(path: str) -> bool:
    """True for any absolute path, on any platform.

    `os.path.isabs` is platform-dependent in BOTH directions, which is why
    relying on it was a real bug caught in CI:

      * On Linux, isabs("C:/Windows/win.ini") is False, so a Windows-style path
        was treated as relative and resolved *inside* the workspace.
      * On Windows, isabs("/etc/passwd") is False, so a POSIX path was likewise
        treated as relative.

    A sandbox whose check changes meaning with the host OS is not a check. Both
    forms are recognised explicitly here.
    """
    if path.startswith(("/", "\\")):          # POSIX absolute, or UNC
        return True
    if len(path) >= 3 and path[1] == ":" and path[2] in "/\\":
        return True                             # C:/... or C:\\...
    return False


def safe_join(root: str, relative: str) -> str:
    """Resolve `relative` inside `root`, or raise SandboxError.

    Refuses: empty paths, NUL bytes, absolute paths in either platform's syntax,
    `~`, and anything that resolves outside root -- including via symlink.
    """
    if not relative or "\x00" in relative:
        raise SandboxError("empty or NUL-containing path")

    rel = relative.replace("\\", "/").strip()
    if _looks_absolute(rel) or rel.startswith("~"):
        raise SandboxError(f"absolute paths are not allowed: {relative!r}")

    resolved = os.path.realpath(os.path.join(root, rel))
    if resolved != root and not resolved.startswith(root + os.sep):
        raise SandboxError(f"path escapes the workspace: {relative!r}")
    return resolved
