import sys
from pathlib import Path

import pytest
import tomlkit

APP = Path(__file__).resolve().parent.parent / "app"
sys.path.insert(0, str(APP))

import core  # noqa: E402


@pytest.fixture
def config(tmp_path):
    """The shipped config.toml (validated), loaded from a copy so tests can't change it."""
    path = tmp_path / "config.toml"
    path.write_text((APP / "config.toml").read_text(encoding="utf-8"), encoding="utf-8")
    config = core.load_config(path)
    # ignore whatever the app last remembered in the real config
    config["general"].update(core.OPTION_DEFAULTS)
    config["general"].update({k: core.GENERAL_DEFAULTS[k] for k in (
        "max_downloads", "filename_video", "filename_audio", "playlist_folder")})
    config["general"]["path"] = str(tmp_path)
    config["general"]["global_args"] = ""
    config["general"]["cookies_browser"] = ""
    return config


@pytest.fixture
def options(config):
    return core.default_options(config)


def make_config(text):
    return tomlkit.parse(text)
