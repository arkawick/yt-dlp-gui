import core
from core import build_command, expected_quality, parse_line, quality_label, supported_options


def args_after(command, flag):
    """Every value passed right after flag."""
    return [b for a, b in zip(command, command[1:]) if a == flag]


# ---- config ---------------------------------------------------------------------


def test_load_config_fills_and_validates(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        '[general]\nresolution = "999"\nmax_downloads = 99\nnotifications = "yes"\n'
        'update_ytdlp = false\n[presets]\nbest = "-f b"\n',
        encoding="utf-8",
    )
    g = core.load_config(path)["general"]
    assert g["resolution"] == "best"  # invalid choice -> default
    assert g["max_downloads"] == 3  # out of range -> default
    assert g["notifications"] is True  # wrong type -> default
    assert g["update_ytdlp"] is False  # false is respected
    assert g["filename_audio"] == "artist_title"  # missing -> default


def test_shipped_presets(config):
    assert set(config["presets"]) >= {"best", "mp4", "mp3", "m4a"}
    assert core.is_audio_preset(config, "mp3")
    assert not core.is_audio_preset(config, "best")
    assert core.audio_format(config, "mp3") == "mp3"
    assert core.audio_format(config, "m4a") == "m4a"


def test_supported_options(config):
    video, mp3, m4a = (supported_options(config, p) for p in ("best", "mp3", "m4a"))
    assert video["resolution"] and video["subtitles"] and not video["bitrate"]
    assert mp3["bitrate"] and mp3["normalize"] and mp3["clean_titles"] and not mp3["resolution"]
    # m4a is copied from YouTube unchanged: bitrate / volume would be ignored by yt-dlp
    assert not m4a["bitrate"] and not m4a["normalize"] and m4a["clean_titles"]


# ---- command building -----------------------------------------------------------


def test_video_quality_sort(config, options):
    options.update(resolution="1080", fps="60", codec="avc")
    cmd = build_command(config, "URL", "D", "best", options)
    assert args_after(cmd, "-S") == ["vcodec:avc,res:1080,fps:60"]  # codec ranks first
    assert cmd[-2:] == ["--", "URL"]


def test_no_sort_for_defaults_or_audio(config, options):
    assert "-S" not in build_command(config, "URL", "D", "best", options)
    options.update(resolution="720")
    assert "-S" not in build_command(config, "URL", "D", "mp3", options)


def test_mp3_bitrate_overrides_preset(config, options):
    options.update(bitrate="320K")
    cmd = build_command(config, "URL", "D", "mp3", options)
    assert args_after(cmd, "--audio-quality")[-1] == "320K"  # last one wins in yt-dlp
    # ignored where yt-dlp would copy the audio
    cmd = build_command(config, "URL", "D", "m4a", options)
    assert "320K" not in cmd


def test_mp3_tags_and_cover(config, options):
    cmd = build_command(config, "URL", "D", "mp3", options)
    assert "--embed-metadata" in cmd and "--embed-thumbnail" in cmd
    assert any("crop=" in a for a in args_after(cmd, "--ppa"))  # square cover
    assert "--parse-metadata" in cmd  # clean titles on by default
    assert args_after(cmd, "-o") == ["%(artist,uploader)s - %(title)s.%(ext)s"]


def test_clean_titles_off(config, options):
    options.update(clean_titles=False)
    cmd = build_command(config, "URL", "D", "mp3", options)
    assert "--parse-metadata" not in cmd and "--replace-in-metadata" not in cmd


def test_playlist_album_and_folder(config, options):
    cmd = build_command(config, "URL", "D", "mp3", options, playlist=True, playlist_items="1,3")
    assert args_after(cmd, "-o")[0].startswith("%(playlist_title,playlist_id)s/")
    assert args_after(cmd, "-I") == ["1,3"]
    meta = args_after(cmd, "--parse-metadata")
    assert any("%(album)s" in m for m in meta) and any("%(track_number)s" in m for m in meta)
    config["general"]["playlist_folder"] = False
    cmd = build_command(config, "URL", "D", "mp3", options, playlist=True)
    assert not args_after(cmd, "-o")[0].startswith("%(playlist")


def test_extras(config, options):
    options.update(subtitles=True, sponsorblock=True, save_cover=True, normalize=True)
    video = build_command(config, "URL", "D", "best", options)
    assert "--embed-subs" in video and "--write-thumbnail" in video
    assert args_after(video, "--sponsorblock-remove") == [core.SPONSORBLOCK]
    assert not any("loudnorm" in a for a in video)
    audio = build_command(config, "URL", "D", "mp3", options)
    assert "--embed-subs" not in audio
    assert args_after(audio, "--sponsorblock-remove") == [core.SPONSORBLOCK + ",music_offtopic"]
    assert any("loudnorm" in a for a in audio)


def test_settings_in_command(config, options):
    config["general"]["cookies_browser"] = "firefox"
    config["general"]["filename_video"] = "title_id"
    config["general"]["global_args"] = "--limit-rate 5M"
    cmd = build_command(config, "URL", "D", "best", options, temp_name="tmpdir")
    assert args_after(cmd, "--cookies-from-browser") == ["firefox"]
    assert args_after(cmd, "-o") == ["%(title)s [%(id)s].%(ext)s"]
    assert args_after(cmd, "-P") == ["D", "temp:tmpdir"]
    assert cmd.index("--limit-rate") > cmd.index("-o")  # user args can override ours


def test_user_temp_path_respected(config, options):
    config["general"]["global_args"] = ["-P", "temp:C:/tmp"]
    cmd = build_command(config, "URL", "D", "best", options, temp_name="tmpdir")
    assert "temp:tmpdir" not in cmd


def test_quality_label(config, options):
    assert quality_label(config, "best", options) == "best"
    options.update(resolution="1080", fps="60", codec="avc", bitrate="320K")
    assert quality_label(config, "best", options) == "best 1080p60 h264"
    assert quality_label(config, "mp3", options) == "mp3 320k"
    assert quality_label(config, "m4a", options) == "m4a"
    options.update(resolution="best", codec="any")
    assert quality_label(config, "best", options) == "best 60fps"


# ---- output parsing -------------------------------------------------------------


def test_parse_progress():
    line = core.DL_MARK + core.SEP.join(
        ["downloading", "10.00MiB", " 42.0%", "1.00MiB/s", "00:06", "Song", "3", "12"])
    kind, data = parse_line(line)
    assert kind == "progress"
    assert data["percent"] == "42.0%" and data["title"] == "Song"
    assert (data["item"], data["count"]) == (3, 12)
    kind, data = parse_line(line.replace(core.SEP + "3" + core.SEP + "12", core.SEP + "NA" + core.SEP + "NA"))
    assert data["item"] is None and data["count"] is None


def test_parse_other_lines():
    assert parse_line(core.PP_MARK + "ExtractAudio" + core.SEP + "started") == (
        "postprocess", ("ExtractAudio", "started"))
    assert parse_line('[MoveFiles] Moving file "C:\\t\\a.mp3" to "C:\\a.mp3"') == ("file", "C:\\a.mp3")
    assert parse_line("[download] C:\\a.mp3 has already been downloaded") == ("file", "C:\\a.mp3")
    assert parse_line('[Merger] Merging formats into "C:\\t\\a.mp4"') == ("dest", "C:\\t\\a.mp4")
    assert parse_line("ERROR: [youtube] abc123: Video unavailable") == ("error", "Video unavailable")
    assert parse_line("WARNING: x")[0] == "warning"
    assert parse_line("[youtube] abc: Downloading webpage") is None


# ---- probing helpers ------------------------------------------------------------

FORMATS = [[2160, 60], [1440, 60], [1080, 60], [720, 60], [720, 30], [360, 30]]


def test_expected_quality():
    opts = {"resolution": "best", "fps": "any"}
    assert expected_quality(FORMATS, opts) == "2160p60"
    assert expected_quality(FORMATS, {**opts, "resolution": "720", "fps": "30"}) == "720p30"
    assert expected_quality(FORMATS, {**opts, "resolution": "480"}) == "360p30"
    assert expected_quality([[144, 15]], {**opts, "resolution": "1080"}) == "144p15"
    assert expected_quality([], opts) == ""


def test_summarize_playlist_and_video():
    playlist = core.summarize_info({
        "_type": "playlist", "title": "Album",
        "entries": [{"id": "a", "title": "One", "duration": 60}, None, {"id": "b"}],
    })
    assert playlist["playlist"] and playlist["title"] == "Album"
    assert [e["title"] for e in playlist["entries"]] == ["One", "b"]
    video = core.summarize_info({
        "title": "Song (Official Video)", "track": "Song", "artist": "Band", "duration": 200,
        "formats": [{"vcodec": "avc1", "height": 1080, "fps": 29.97},
                    {"vcodec": "none", "acodec": "opus"}],
    })
    assert video["title"] == "Song" and video["uploader"] == "Band"
    assert video["formats"] == [[1080, 30]]


def test_helpers():
    assert core.format_duration(3725) == "1:02:05"
    assert core.format_duration(65) == "1:05"
    assert core.format_duration(None) == ""
    assert core.find_urls("see https://a.b/c and http://x.y z") == ["https://a.b/c", "http://x.y"]
