# Security and reliability boundaries

## Enforced by the application

- Only list/read tools are exposed; arbitrary command execution is not available.
- The default workspace is a dedicated workspace directory, not the repository root.
- Absolute paths, parent traversal, home expansion, NUL bytes, hidden paths and resolved symlink escapes are rejected.
- File reads are capped at 100,000 bytes and read at most one byte beyond the configured cap to detect oversize files.
- Directory listings are capped at 200 visible entries.
- Requests are limited to four tool steps.
- Invalid tool/path inputs return HTTP 400 rather than an unhandled server error.

## What this is not

This is application-level path confinement, not an OS security boundary. It does not eliminate time-of-check/time-of-use races against a malicious process that can mutate the workspace, nor does it isolate CPU, memory, network access or the process itself. Do not place credentials or private files in the workspace. For hostile workloads, run in an unprivileged container/VM with OS-level restrictions and a read-only mount.
