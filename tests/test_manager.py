"""DownloadManager with a fake yt-dlp: scheduling, pause/resume, playlists, errors, history."""

import threading
import time

import pytest

import core
import manager as mgr


class FakeYtDlp:
    """Stands in for core.run_process / core.probe."""

    def __init__(self):
        self.started = []
        self.release = threading.Event()  # downloads block until set
        self.fail_urls = set()
        self.playlists = {}

    def probe(self, config, url):
        if url in self.playlists:
            return core.summarize_info({"_type": "playlist", "title": "PL",
                                        "entries": [{"title": t} for t in self.playlists[url]]})
        return core.summarize_info({"title": f"title of {url}", "formats": []})

    def run_process(self, command, on_line, on_start=None):
        url = command[-1]
        self.started.append(command)

        class Proc:
            pid = 0
            returncode = None

            def poll(self):
                return self.returncode

            def terminate(self):
                self.returncode = -15

        proc = Proc()
        if on_start:
            on_start(proc)
        on_line(core.DL_MARK + core.SEP.join(
            ["downloading", "1MiB", "50.0%", "1MiB/s", "00:01", "T", "NA", "NA"]))
        while not self.release.wait(0.02):
            if proc.returncode is not None:
                return proc.returncode
        if url in self.fail_urls:
            on_line("ERROR: [youtube] x: Video unavailable")
            return 1
        on_line(f'[MoveFiles] Moving file "tmp" to "/out/{url}.mp3"')
        return 0


@pytest.fixture
def fake(monkeypatch):
    fake = FakeYtDlp()
    monkeypatch.setattr(mgr, "run_process", fake.run_process)
    monkeypatch.setattr(mgr, "probe", fake.probe)
    # Windows taskkill can't kill the fake process; terminate it directly
    monkeypatch.setattr(mgr.DownloadManager, "_kill",
                        lambda self, job, reason: (setattr(job, "_stop", reason),
                                                   job._proc and job._proc.terminate()))
    return fake


def wait_for(cond, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


def new_manager(config, tmp_path, **kwargs):
    events = []
    m = mgr.DownloadManager(config, lambda e, j: events.append((e, j.id)),
                            history_path=tmp_path / "history.json", **kwargs)
    return m, events


def test_parallel_limit(config, options, tmp_path, fake):
    config["general"]["max_downloads"] = 2
    m, _ = new_manager(config, tmp_path)
    jobs = [m.add(f"u{i}", str(tmp_path), "mp3", options) for i in range(4)]
    m.start()
    assert wait_for(lambda: sum(j.status == mgr.DOWNLOADING for j in jobs) == 2)
    time.sleep(0.1)
    assert sum(j.active for j in jobs) == 2
    assert sum(j.status == mgr.WAITING for j in jobs) == 2
    fake.release.set()
    assert wait_for(lambda: all(j.status == mgr.FINISHED for j in jobs))
    assert jobs[0].filepath == "/out/u0.mp3" and jobs[0].title == "T"


def test_error_message_and_retry(config, options, tmp_path, fake):
    m, events = new_manager(config, tmp_path)
    fake.fail_urls.add("bad")
    fake.release.set()
    job = m.add("bad", str(tmp_path), "mp3", options)
    m.start()
    assert wait_for(lambda: job.status == mgr.ERROR)
    assert job.error == "Video unavailable"
    assert ("failed", job.id) in events
    fake.fail_urls.clear()
    m.start([job])
    assert wait_for(lambda: job.status == mgr.FINISHED)
    assert job.error == ""


def test_pause_resume_keeps_temp(config, options, tmp_path, fake):
    m, _ = new_manager(config, tmp_path)
    job = m.add("u", str(tmp_path), "mp3", options)
    m.start()
    assert wait_for(lambda: job.status == mgr.DOWNLOADING)
    job.temp_dir.mkdir()
    (job.temp_dir / "partial.part").write_text("x")
    m.pause(job)
    assert wait_for(lambda: job.status == mgr.PAUSED)
    assert job.temp_dir.exists()  # kept for resuming
    temp_args = [a for a in fake.started[-1] if a.startswith("temp:")]
    m.resume(job)
    fake.release.set()
    assert wait_for(lambda: job.status == mgr.FINISHED)
    assert [a for a in fake.started[-1] if a.startswith("temp:")] == temp_args  # same folder
    assert not job.temp_dir.exists()  # cleaned up after success


def test_remove_running_deletes_temp(config, options, tmp_path, fake):
    m, events = new_manager(config, tmp_path)
    job = m.add("u", str(tmp_path), "mp3", options)
    m.start()
    assert wait_for(lambda: job.status == mgr.DOWNLOADING)
    job.temp_dir.mkdir()
    m.remove(job)
    assert job.id not in m.jobs and ("removed", job.id) in events
    assert wait_for(lambda: not job.temp_dir.exists())


def test_playlist_waits_for_choice(config, options, tmp_path, fake):
    fake.playlists["pl"] = ["a", "b", "c"]
    fake.release.set()
    m, events = new_manager(config, tmp_path)
    job = m.add("pl", str(tmp_path), "mp3", options)
    m.start()  # started before the probe finished
    assert wait_for(lambda: ("playlist", job.id) in events)
    time.sleep(0.2)
    assert job.status == mgr.WAITING and not fake.started  # nothing downloads yet
    m.choose_entries(job, [1, 3])
    assert wait_for(lambda: job.status == mgr.FINISHED)
    cmd = fake.started[-1]
    assert cmd[cmd.index("-I") + 1] == "1,3"
    assert any(a.startswith("%(playlist_title") for a in cmd)


def test_playlist_batch_mode_downloads_all(config, options, tmp_path, fake):
    fake.playlists["pl"] = ["a", "b"]
    fake.release.set()
    m, events = new_manager(config, tmp_path, ask_playlist=False)
    job = m.add("pl", str(tmp_path), "mp3", options)
    m.start()
    assert wait_for(lambda: job.status == mgr.FINISHED)
    assert ("playlist", job.id) not in events and "-I" not in fake.started[-1]


def test_history_roundtrip(config, options, tmp_path, fake):
    m, _ = new_manager(config, tmp_path)
    done = m.add("done", str(tmp_path), "mp3", options)
    running = m.add("running", str(tmp_path), "best", options)
    m.start([done])
    fake.release.set()
    assert wait_for(lambda: done.status == mgr.FINISHED)
    fake.release.clear()
    m.start([running])
    assert wait_for(lambda: running.status == mgr.DOWNLOADING)
    m.shutdown()  # quitting pauses running downloads

    m2, events = new_manager(config, tmp_path)
    m2.load_history()
    statuses = {j.url: j.status for j in m2.jobs.values()}
    assert statuses == {"done": mgr.FINISHED, "running": mgr.PAUSED}
    assert m2.jobs[done.id].filepath == "/out/done.mp3"
    new = m2.add("new", str(tmp_path), "mp3", options)
    assert new.id == max(done.id, running.id) + 1


def test_label_shows_expected_quality(config, options, tmp_path, fake):
    m, _ = new_manager(config, tmp_path)
    job = m.add("u", str(tmp_path), "best", {**options, "resolution": "1080"})
    job.info = {"playlist": False, "formats": [[720, 30], [360, 30]], "entries": []}
    assert m.label(job) == "best 1080p → 720p30"
    job.options["resolution"] = "720"
    assert m.label(job) == "best 720p"
