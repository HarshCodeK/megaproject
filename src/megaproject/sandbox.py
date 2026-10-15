from pathlib import Path


class SandboxError(ValueError):
    pass


class Workspace:
    MAX_READ_BYTES = 100_000
    MAX_LIST_ENTRIES = 200
    MAX_PATH_CHARS = 240

    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, relative):
        if not isinstance(relative, str) or not relative or len(relative) > self.MAX_PATH_CHARS or "\x00" in relative:
            raise SandboxError("path is empty, contains NUL, or exceeds the path-length limit")
        candidate = Path(relative)
        if candidate.is_absolute() or ".." in candidate.parts or relative.startswith("~"):
            raise SandboxError("unsafe path")
        if any(part.startswith(".") for part in candidate.parts if part not in (".", "..")):
            raise SandboxError("hidden paths are not accessible")
        resolved = (self.root / candidate).resolve()
        if resolved != self.root and self.root not in resolved.parents:
            raise SandboxError("outside workspace")
        relative_parts = resolved.relative_to(self.root).parts
        if any(part.startswith(".") for part in relative_parts):
            raise SandboxError("hidden paths are not accessible")
        return resolved

    def read(self, relative, max_bytes=MAX_READ_BYTES):
        if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or not 1 <= max_bytes <= self.MAX_READ_BYTES:
            raise SandboxError("read limit is outside the permitted range")
        path = self.path(relative)
        if not path.is_file():
            raise SandboxError("path is not a regular file")
        try:
            with path.open("rb") as stream:
                data = stream.read(max_bytes + 1)
        except OSError as exc:
            raise SandboxError("file could not be read") from exc
        if len(data) > max_bytes:
            raise SandboxError("file too large")
        return data.decode("utf-8", "replace")

    def list(self, relative="."):
        path = self.path(relative)
        if not path.is_dir():
            raise SandboxError("path is not a directory")
        try:
            entries = sorted(item.name for item in path.iterdir() if not item.name.startswith("."))
        except OSError as exc:
            raise SandboxError("directory could not be listed") from exc
        if len(entries) > self.MAX_LIST_ENTRIES:
            raise SandboxError("directory has too many entries to list safely")
        return entries
