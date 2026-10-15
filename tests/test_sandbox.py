import pytest

from megaproject.sandbox import SandboxError, Workspace


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


def test_hidden_files_are_not_accessible(tmp_path):
    (tmp_path / ".env").write_text("secret")
    (tmp_path / "visible.txt").write_text("safe")
    workspace = Workspace(tmp_path)
    with pytest.raises(SandboxError):
        workspace.read(".env")
    assert workspace.list() == ["visible.txt"]


def test_file_size_is_checked_before_full_read(tmp_path):
    (tmp_path / "large.txt").write_bytes(b"x" * 100_001)
    with pytest.raises(SandboxError, match="file too large"):
        Workspace(tmp_path).read("large.txt")


def test_directory_listing_is_bounded(tmp_path):
    for index in range(201):
        (tmp_path / f"file-{index:03}.txt").touch()
    with pytest.raises(SandboxError, match="too many entries"):
        Workspace(tmp_path).list()


def test_nul_path_is_rejected(tmp_path):
    with pytest.raises(SandboxError, match="NUL"):
        Workspace(tmp_path).path("\x00")


def test_read_limit_cannot_be_unbounded(tmp_path):
    with pytest.raises(SandboxError, match="read limit"):
        Workspace(tmp_path).read("x.txt", max_bytes=100_001)
