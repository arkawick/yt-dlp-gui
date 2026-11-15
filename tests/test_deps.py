"""Dependency installer: archive extraction and platform sources (no network)."""

import io
import tarfile
import zipfile

import pytest

import deps


@pytest.fixture
def bin_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(deps, "BIN_DIR", tmp_path)
    return tmp_path


def fake_download(payload):
    def download(url, progress):
        progress("50%")
        return io.BytesIO(payload)
    return download


def test_zip_with_nested_binaries(bin_dir, monkeypatch):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("ffmpeg-master-latest-win64-gpl/bin/ffmpeg.exe", b"FFMPEG")
        zf.writestr("ffmpeg-master-latest-win64-gpl/bin/ffprobe.exe", b"FFPROBE")
        zf.writestr("ffmpeg-master-latest-win64-gpl/doc/readme.txt", b"x")
    monkeypatch.setattr(deps, "download", fake_download(buf.getvalue()))
    monkeypatch.setattr(deps, "exe_name", lambda tool: tool + ".exe")
    messages = []
    deps.fetch("https://x/ffmpeg.zip", {"ffmpeg.exe": "ffmpeg", "ffprobe.exe": "ffprobe"}, messages.append)
    assert (bin_dir / "ffmpeg.exe").read_bytes() == b"FFMPEG"
    assert (bin_dir / "ffprobe.exe").read_bytes() == b"FFPROBE"
    assert messages


def test_tar_xz(bin_dir, monkeypatch):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:xz") as tf:
        for name, data in (("pkg/bin/ffmpeg", b"A"), ("pkg/bin/ffprobe", b"B")):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    monkeypatch.setattr(deps, "download", fake_download(buf.getvalue()))
    monkeypatch.setattr(deps, "exe_name", lambda tool: tool)
    deps.fetch("https://x/ffmpeg.tar.xz", {"ffmpeg": "ffmpeg", "ffprobe": "ffprobe"}, print)
    assert (bin_dir / "ffmpeg").read_bytes() == b"A"
    assert (bin_dir / "ffprobe").read_bytes() == b"B"


def test_plain_binary(bin_dir, monkeypatch):
    monkeypatch.setattr(deps, "download", fake_download(b"YTDLP"))
    monkeypatch.setattr(deps, "exe_name", lambda tool: tool)
    deps.fetch("https://x/yt-dlp_linux", {"yt-dlp_linux": "yt-dlp"}, print)
    assert (bin_dir / "yt-dlp").read_bytes() == b"YTDLP"


@pytest.mark.parametrize("system,machine", [
    ("Windows", "AMD64"), ("Linux", "x86_64"), ("Linux", "aarch64"),
    ("Darwin", "arm64"), ("Darwin", "x86_64"),
])
def test_sources_cover_every_tool(monkeypatch, system, machine):
    monkeypatch.setattr(deps.platform, "system", lambda: system)
    monkeypatch.setattr(deps.platform, "machine", lambda: machine)
    sources = deps.sources()
    assert set(sources) == {"yt-dlp", "ffmpeg", "ffprobe", "deno"}
    for tool, entries in sources.items():
        assert any(tool in members.values() for _, members in entries)
    assert "4.1" not in str(sources)  # no more ancient ffmpeg


def test_ffmpeg_archive_fetched_once(bin_dir, monkeypatch):
    monkeypatch.setattr(deps.platform, "system", lambda: "Windows")
    monkeypatch.setattr(deps, "find_missing", lambda: ["ffmpeg", "ffprobe"])
    fetched = []
    monkeypatch.setattr(deps, "fetch", lambda url, members, progress: fetched.append(url))
    deps.ensure_dependencies(update_ytdlp=False, progress=print)
    assert len(fetched) == 1 and fetched[0].endswith("win64-gpl.zip")
