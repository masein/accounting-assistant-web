"""The nightly backup and the live copy's file sync say "done" only when it is (review 2026-11-28, scenarios CW1, CW2).

The real scripts run here against stand-in ``pg_dump``, ``tar``, ``ssh`` and ``rsync`` (and, for the backup's daily
loop, ``date`` and ``sleep`` on a clock the test moves). Each stand-in fails when told to, the way the real one
would: a dump cut off part-way, a server that can't be reached.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKUP_LOOP = ROOT / "scripts" / "backup-loop.sh"
SYNC = ROOT / "ops" / "live-copy" / "scripts" / "sync.sh"

# pg_dump -Fc -Z 6 -f FILE: writes part of a dump, then fails while STUB_PG_FAILS says so (a count, used up one per
# call); every call's time (the fake clock, or the real one) goes in STUB_CALLS
PG_DUMP = r"""#!/bin/sh
while [ $# -gt 0 ]; do [ "$1" = -f ] && out=$2; shift; done
echo "$(cat "$FAKE_NOW" 2>/dev/null || date +%s)" >> "$STUB_CALLS"
echo "PGDMP partial" > "$out"
left=$(cat "$STUB_PG_FAILS" 2>/dev/null || echo 0)
if [ "$left" -gt 0 ]; then echo $((left - 1)) > "$STUB_PG_FAILS"; echo "pg_dump: error: connection lost" >&2; exit 1; fi
echo "PGDMP complete" >> "$out"
"""

# tar -czf FILE -C DIR .: fails (after writing part of the file) for the DIR named in STUB_TAR_FAIL
TAR = r"""#!/bin/sh
out=""; dir=""
while [ $# -gt 0 ]; do
  case "$1" in -czf) out=$2; shift ;; -C) dir=$2; shift ;; esac
  shift
done
echo "partial" > "$out"
if [ -n "${STUB_TAR_FAIL:-}" ] && [ "$dir" = "$STUB_TAR_FAIL" ]; then echo "tar: write error" >&2; exit 2; fi
"""

# the backup loop's clock: date +FORMAT and sleep N on FAKE_NOW (epoch seconds, UTC); sleep stops the loop (exit 99,
# which `set -e` passes on) after STUB_MAX_SLEEPS
DATE = r"""#!/bin/sh
now=$(cat "$FAKE_NOW")
case "$1" in +%s) echo "$now"; exit 0 ;; esac
/bin/date -u -d "@$now" "$1" 2>/dev/null || /bin/date -u -r "$now" "$1"
"""
SLEEP = r"""#!/bin/sh
echo $(( $(cat "$FAKE_NOW") + $1 )) > "$FAKE_NOW"
n=$(( $(cat "$STUB_SLEEPS" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$STUB_SLEEPS"
[ "$n" -ge "$STUB_MAX_SLEEPS" ] && exit 99
exit 0
"""


def _stubs(where: Path, **bodies: str) -> Path:
    where.mkdir(exist_ok=True)
    for name, body in bodies.items():
        (where / name).write_text(body)
        (where / name).chmod(0o755)
    return where


@pytest.fixture()
def backup(tmp_path):
    if shutil.which("sh") is None:
        pytest.skip("no sh")
    bin_ = _stubs(tmp_path / "bin", pg_dump=PG_DUMP, tar=TAR)
    out, up, priv = tmp_path / "backups", tmp_path / "uploads", tmp_path / "private_uploads"
    for d in (out, up, priv):
        d.mkdir()

    class Backup:
        dir = out
        calls_file = tmp_path / "calls.log"
        env = {"PATH": f"{bin_}:/usr/bin:/bin", "HOME": str(tmp_path), "BACKUP_INTO": str(out),
               "BACKUP_UPLOADS": str(up), "BACKUP_PRIVATE_UPLOADS": str(priv),
               "STUB_CALLS": str(tmp_path / "calls.log"), "STUB_PG_FAILS": str(tmp_path / "pg_fails")}

        def fail_pg_dump(self, times):
            (tmp_path / "pg_fails").write_text(str(times))

        def once(self, **extra):
            return subprocess.run(["sh", str(BACKUP_LOOP), "once"], env={**self.env, **extra},
                                  capture_output=True, text=True, timeout=60)

        def files(self):
            return sorted(p.name.split("-2")[0] + ("" if not p.name.endswith(".tmp") else ".tmp")
                          for p in out.iterdir())

    return Backup()


def test_a_backup_that_works(backup):
    """CW1: every part done → exit 0, three files, nothing half-written."""
    r = backup.once()
    assert r.returncode == 0, r.stderr
    assert backup.files() == ["db", "private-uploads", "uploads"]
    assert "done:" in r.stdout and "file(s) kept" in r.stdout


def test_a_failed_dump_fails_the_backup_and_leaves_nothing_half_written(backup):
    """CW1: pg_dump fails part-way → the backup fails (it used to be logged as done) and its .tmp is removed."""
    backup.fail_pg_dump(1)
    r = backup.once()
    assert r.returncode == 1
    assert "FAILED: pg_dump writing db-" in r.stderr and "done" not in r.stdout
    assert backup.files() == []


def test_a_failed_tar_fails_the_backup(backup):
    """CW1: the dump worked, the private uploads' tar didn't → failed, and no .tmp of that tar is left."""
    r = backup.once(STUB_TAR_FAIL=backup.env["BACKUP_PRIVATE_UPLOADS"])
    assert r.returncode == 1 and "FAILED: tar writing private-uploads-" in r.stderr
    assert backup.files() == ["db", "uploads"]


def test_a_leftover_tmp_is_pruned_once_stale(backup):
    """CW1: a .tmp from a backup killed part-way is removed after 6 hours; a recent one (still being written by
    another run) is left alone."""
    stale, recent = backup.dir / "db-20261127-020000.dump.tmp", backup.dir / "db-20261128-020000.dump.tmp"
    for f in (stale, recent):
        f.write_text("partial")
    t = time.time() - 7 * 3600
    os.utime(stale, (t, t))
    assert backup.once().returncode == 0
    assert not stale.exists() and recent.exists()


def test_the_daily_loop_marks_the_day_done_only_on_success_and_retries_hourly(backup, tmp_path):
    """CW1: at BACKUP_HOUR a failing dump is not counted as the day's backup: it is tried again an hour later (not
    every 5 minutes, and not only tomorrow), and once it works nothing more runs that day."""
    _stubs(tmp_path / "bin", date=DATE, sleep=SLEEP)
    start = 1_795_829_400                       # 2026-11-28 01:30 UTC
    (tmp_path / "now").write_text(str(start))
    backup.fail_pg_dump(1)
    env = {**backup.env, "FAKE_NOW": str(tmp_path / "now"), "STUB_SLEEPS": str(tmp_path / "sleeps"),
           "STUB_MAX_SLEEPS": str(3 * 12), "BACKUP_HOUR": "02"}          # three hours of 5-minute rounds
    r = subprocess.run(["sh", str(BACKUP_LOOP)], env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 99, r.stderr                                    # the stand-in sleep ended it
    calls = [int(x) - start for x in backup.calls_file.read_text().split()]
    assert calls == [30 * 60, 90 * 60]           # 02:00 failed, 03:00 worked; nothing between, nothing after
    assert "FAILED — trying again in 60 minutes" in r.stderr
    assert backup.files() == ["db", "private-uploads", "uploads"]


def test_a_failed_backup_on_start_is_retried_and_does_not_stop_the_container(backup, tmp_path):
    """CW1: BACKUP_ON_START=1 and the dump fails → logged as FAILED, the loop keeps running, an hour later it works."""
    _stubs(tmp_path / "bin", date=DATE, sleep=SLEEP)
    start = 1_795_857_600                       # 2026-11-28 09:20 UTC, hours from BACKUP_HOUR
    (tmp_path / "now").write_text(str(start))
    backup.fail_pg_dump(1)
    env = {**backup.env, "FAKE_NOW": str(tmp_path / "now"), "STUB_SLEEPS": str(tmp_path / "sleeps"),
           "STUB_MAX_SLEEPS": "14", "BACKUP_HOUR": "02", "BACKUP_ON_START": "1"}
    r = subprocess.run(["sh", str(BACKUP_LOOP)], env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 99, r.stderr
    calls = [int(x) - start for x in backup.calls_file.read_text().split()]
    assert calls == [0, 3600]
    assert backup.files() == ["db", "private-uploads", "uploads"]


def test_the_backup_scripts_parse():
    subprocess.run(["sh", "-n", str(BACKUP_LOOP)], check=True)
    subprocess.run(["bash", "-n", str(ROOT / "scripts" / "backup.sh")], check=True)
    subprocess.run(["sh", "-n", str(SYNC)], check=True)
    text = (ROOT / "scripts" / "backup.sh").read_text(encoding="utf-8")
    assert "trap finish EXIT" in text and '.dump.tmp"' in text      # the host's own backup: no truncated dump left


# --- the live copy's file sync ---------------------------------------------------------------------------------

# ssh … "has NAME": exits STUB_HAS_<NAME> (default 0: the folder is there; 1: not on this server; 255: ssh couldn't
# reach the server)
SSH = r"""#!/bin/sh
for last; do :; done
case "$last" in
  "has "*) name=$(echo "${last#has }" | tr - _); eval "exit \${STUB_HAS_$name:-${STUB_HAS:-0}}" ;;
esac
exit 0
"""
# rsync … REMOTE:SRC DEST/: exits STUB_RSYNC_FAIL's status for the folder named there
RSYNC = r"""#!/bin/sh
for last; do :; done
echo "$last" >> "$STUB_RSYNCS"
case "$last" in *"/${STUB_RSYNC_FAIL:-none}/") echo "rsync: connection unexpectedly closed" >&2; exit 12 ;; esac
exit 0
"""


@pytest.fixture()
def sync(tmp_path):
    bin_ = _stubs(tmp_path / "bin", ssh=SSH, rsync=RSYNC)
    files = tmp_path / "files"
    files.mkdir()

    class Sync:
        stamp = files / ".last-sync"
        rsyncs_file = tmp_path / "rsyncs.log"

        def once(self, **stub):
            env = {"PATH": f"{bin_}:/usr/bin:/bin", "HOME": str(tmp_path), "SSH_USER": "copy",
                   "SSH_HOST": "server.example", "SYNC_INTO": str(files), "STUB_RSYNCS": str(self.rsyncs_file), **stub}
            if self.rsyncs_file.exists():
                self.rsyncs_file.unlink()
            return subprocess.run(["sh", str(SYNC), "once"], env=env, capture_output=True, text=True, timeout=60)

        def synced(self):
            if not self.rsyncs_file.exists():
                return []
            return [Path(line).name for line in self.rsyncs_file.read_text().split()]

    return Sync()


def test_every_folder_synced_stamps_last_sync(sync):
    """CW2: all three folders copied → .last-sync written."""
    r = sync.once()
    assert r.returncode == 0, r.stdout + r.stderr
    assert sync.synced() == ["uploads", "private_uploads", "server-backups"]
    assert sync.stamp.read_text().startswith("20")


def test_a_server_that_cant_be_reached_never_stamps_a_sync(sync):
    """CW2: ssh can't reach the server (exit 255) → the round fails and .last-sync is not touched, so the watchdog's
    file check turns red during an outage. Before, each folder was skipped as "not on this server" and the round
    was stamped as a full sync."""
    sync.stamp.write_text("2026-11-28T01:00:00Z\n")
    r = sync.once(STUB_HAS="255")
    assert r.returncode == 1
    assert sync.stamp.read_text() == "2026-11-28T01:00:00Z\n"
    assert r.stdout.count("couldn't reach the server (ssh exit 255)") == 3
    assert sync.synced() == []


def test_a_folder_not_on_the_server_is_not_a_failure(sync):
    """CW2: the gate says a folder isn't there (exit 1, e.g. no private uploads yet) → the others sync, stamped."""
    r = sync.once(STUB_HAS_private_uploads="1")
    assert r.returncode == 0
    assert sync.synced() == ["uploads", "server-backups"] and sync.stamp.exists()


def test_one_folder_that_failed_to_copy_fails_the_round(sync):
    """CW2: rsync of one folder fails → no stamp, though the other two were copied."""
    r = sync.once(STUB_RSYNC_FAIL="server-backups")
    assert r.returncode == 1 and "sync of server-backups failed" in r.stdout
    assert not sync.stamp.exists()
