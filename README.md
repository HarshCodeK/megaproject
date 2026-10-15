# MegaProject — sandboxed tool agent

Small read-only tool executor where every requested path is treated as untrusted input and constrained to a dedicated workspace.

The default workspace is `workspace/`, not the repository root. Absolute paths, parent traversal, home expansion, hidden paths, NUL bytes and resolved symlink escapes are rejected. File reads and directory listings are bounded; runs accept at most four steps.

This is application-level path confinement, not OS-level isolation. See `docs/RELIABILITY_AND_SECURITY.md` for limits and deployment requirements.

## Test
`pip install -e . && pytest -q`

## Interview notes
Resolve before checking; allowlist tools; cap agent rounds; keep retrieval separate from the security boundary.

## License
MIT.
