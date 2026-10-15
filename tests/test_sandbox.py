import pytest
from megaproject.sandbox import Workspace, SandboxError


def test_read_inside(tmp_path):
    (tmp_path / "a.txt").write_text("hello")
    assert Workspace(tmp_path).read("a.txt") == "hello"


def test_escape(tmp_path):
    with pytest.raises(SandboxError):
        Workspace(tmp_path).read("../secret")


def test_symlink_escape(tmp_path):
    outside = tmp_path.parent / "outside-x"
    outside.write_text("secret")
    (tmp_path / "link").symlink_to(outside)
    with pytest.raises(SandboxError):
        Workspace(tmp_path).read("link")


def test_hidden_files_are_not_readable_or_listed(tmp_path):
    (tmp_path / ".env").write_text("secret")
    (tmp_path / "visible.txt").write_text("safe")
    workspace = Workspace(tmp_path)
    with pytest.raises(SandboxError):
        workspace.read(".env")
    assert "visible.txt" in workspace.list()
    assert ".env" not in workspace.list()


def test_read_cap_is_checked_before_unbounded_read(tmp_path):
    (tmp_path / "large.bin").write_bytes(b"x" * 100_001)
    with pytest.raises(SandboxError, match="file too large"):
        Workspace(tmp_path).read("large.bin")


def test_list_has_a_bounded_result(tmp_path):
    for index in range(201):
        (tmp_path / f"f{index:03}.txt").touch()
    with pytest.raises(SandboxError, match="too many entries"):
        Workspace(tmp_path).list()


def test_nul_in_path_is_rejected_before_path_operations(tmp_path):
    with pytest.raises(SandboxError, match="NUL"):
        Workspace(tmp_path).path("\x00")


@pytest.mark.parametrize("limit", [0, -1, 100_001, True, 1.5])
def test_invalid_read_limits_are_rejected(tmp_path, limit):
    (tmp_path / "safe.txt").write_text("safe")
    with pytest.raises(SandboxError, match="read limit"):
        Workspace(tmp_path).read("safe.txt", max_bytes=limit)
