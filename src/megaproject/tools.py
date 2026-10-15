from .sandbox import SandboxError, Workspace

ALLOWED = {"list", "read"}


def dispatch(ws: Workspace, name: str, args: dict):
    if name not in ALLOWED:
        raise SandboxError("tool_not_allowed")
    if not isinstance(args, dict):
        raise SandboxError("tool_arguments_must_be_an_object")
    path = args.get("path", ".")
    if not isinstance(path, str):
        raise SandboxError("path_must_be_a_string")
    if name == "list":
        return ws.list(path)
    if "path" not in args:
        raise SandboxError("path_required")
    return ws.read(path)
