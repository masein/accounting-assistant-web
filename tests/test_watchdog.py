"""The watchdog on the Mac that keeps the live copy and carries the Telegram relay (ops/watchdog, DEPLOY.md §15).

The real script runs here against stand-in ``docker`` and ``curl`` commands: production, the relay, the copy's link,
files and dumps, and Telegram each made to fail and come back, the clock moved with WATCHDOG_NOW. What it sends to
Telegram is what the stand-in curl was asked to post.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "ops" / "watchdog" / "watchdog.sh"
T0 = 1_795_400_000            # a moment, in seconds; the files' times are set around it

DOCKER = r"""#!/bin/bash
echo "docker $*" >> "$STUB_ARGV"
case "$1" in
  inspect) echo "${STUB_RELAY_STATE:-running}" ;;
  exec)
    case "$*" in
      *pg_stat_wal_receiver*) [ "${STUB_WAL-streaming|5}" = none ] || echo "${STUB_WAL-streaming|5}" ;;
      *messenger_links*) [ -n "${STUB_CHAT:-}" ] && echo "$STUB_CHAT" ;;
      *pg_isready*) exit "${STUB_PG_READY:-0}" ;;
    esac ;;
esac
exit 0
"""

CURL = r"""#!/bin/bash
echo "curl $*" >> "$STUB_ARGV"
for a in "$@"; do
  case "$a" in
    */health)
      if [ "${STUB_PROD:-up}" = down ]; then echo "curl: (7) Failed to connect" >&2; exit 7; fi
      echo "${STUB_HEALTH:-{\"status\":\"ok\",\"database\":\"connected\"\}}"; exit 0 ;;
  esac
done
url=$(cat)                                     # -K -: the config, with the bot's url, on stdin
echo "$url" >> "$STUB_CONFIG"
if [ "${STUB_TG:-up}" = down ]; then echo "curl: (6) Could not resolve host" >&2; exit 6; fi
chat="" text=""
while [ $# -gt 0 ]; do
  if [ "$1" = --data-urlencode ]; then
    case "$2" in chat_id=*) chat=${2#chat_id=} ;; text=*) text=${2#text=} ;; esac
    shift
  fi
  shift
done
printf '%s\t%s\n' "$chat" "$text" >> "$STUB_SENT"
echo '{"ok":true,"result":{}}'
"""


@pytest.fixture()
def dog(tmp_path):
    if shutil.which("bash") is None:
        pytest.skip("no bash")
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    for name, body in (("docker", DOCKER), ("curl", CURL)):
        (bin_ / name).write_text(body)
        (bin_ / name).chmod(0o755)
    relay, copy, state = tmp_path / "relay", tmp_path / "copy", tmp_path / "state"
    (relay / "state").mkdir(parents=True)
    (copy / "data" / "files").mkdir(parents=True)
    (copy / "data" / "dumps" / "hourly").mkdir(parents=True)
    (relay / "token.env").write_text("TELEGRAM_BOT_TOKEN=123456:SECRET-token\n")
    files = {"alive": relay / "state" / "alive", "sync": copy / "data" / "files" / ".last-sync",
             "dump": copy / "data" / "dumps" / "hourly" / "db-1.dump"}
    for f in files.values():
        f.write_text("")

    class Dog:
        sent_file = tmp_path / "sent.tsv"
        argv_file = tmp_path / "argv.log"
        config_file = tmp_path / "config.log"
        env = {"PATH": f"{bin_}:/usr/bin:/bin", "HOME": str(tmp_path), "WATCHDOG_ENV": str(tmp_path / "none.env"),
               "RELAY_DIR": str(relay), "COPY_DIR": str(copy), "STATE_DIR": str(state),
               "ALERT_CHAT_ID": "777", "DISK_MIN_GB": "0", "APP_URL": "https://books.example",
               "STUB_SENT": str(tmp_path / "sent.tsv"), "STUB_ARGV": str(tmp_path / "argv.log"),
               "STUB_CONFIG": str(tmp_path / "config.log")}

        def fresh(self, at, **ages):
            """Every heartbeat, sync and dump this many seconds before ``at`` (a minute, unless given)."""
            for key, path in files.items():
                t = at - ages.get(key, 60)
                os.utime(path, (t, t))

        def run(self, at, *args, **stub):
            self.fresh(at, **{k: stub.pop(k) for k in list(stub) if k in files})
            env = {**self.env, "WATCHDOG_NOW": str(at), **{k: str(v) for k, v in stub.items()}}
            before = self.sent()
            out = subprocess.run(["bash", str(SCRIPT), *args], env=env, capture_output=True, text=True, timeout=60)
            assert out.returncode == 0, out.stderr
            self.stdout = out.stdout
            return self.sent()[len(before):]

        def sent(self):
            if not self.sent_file.exists():
                return []
            return [tuple(line.split("\t", 1)) for line in self.sent_file.read_text().splitlines()]

    return Dog()


def test_all_well_says_nothing(dog):
    assert dog.run(T0) == []
    assert dog.run(T0 + 300) == []


def test_production_down_is_said_once_reminded_and_said_back(dog):
    dog.env["GAP_SECONDS"] = "100000"                                 # the rounds between aren't run here
    assert dog.run(T0, STUB_PROD="down") == []                       # one round: a blip
    [(chat, text)] = dog.run(T0 + 300, STUB_PROD="down")
    assert chat == "777" and text.startswith("⚠️ The accounting site isn't answering (https://books.example)")
    assert "Failed to connect" in text
    assert dog.run(T0 + 600, STUB_PROD="down") == []                 # not every round
    [(_, again)] = dog.run(T0 + 300 + 21600, STUB_PROD="down")       # six hours on
    assert again.startswith("(still, since ") and "isn't answering" in again
    [(_, back)] = dog.run(T0 + 300 + 21900)
    assert back.startswith("✅ Back to normal: the accounting site (after 6 h")
    assert dog.run(T0 + 300 + 22200) == []


def test_production_answering_badly(dog):
    dog.run(T0, STUB_HEALTH='{"status":"ok","database":"down"}')
    [(_, text)] = dog.run(T0 + 300, STUB_HEALTH='{"status":"ok","database":"down"}')
    assert "its database is not connected" in text


def test_the_relay_stopped_or_stuck(dog):
    dog.run(T0, STUB_RELAY_STATE="exited")
    [(_, text)] = dog.run(T0 + 300, STUB_RELAY_STATE="exited")
    assert text.startswith("⚠️ The Telegram bot is down: the relay on this Mac is exited.")
    dog.run(T0 + 600)                                                  # back
    dog.run(T0 + 900, alive=1200)
    [(_, stuck)] = dog.run(T0 + 1200, alive=1500)
    assert "has not polled Telegram for 25 min" in stuck


@pytest.mark.parametrize("stub, said", [
    ({"STUB_WAL": "startup|3"}, "the link is startup"),
    ({"STUB_WAL": "streaming|900"}, "nothing from the server for 15 min"),
    ({"STUB_WAL": "none"}, "not connected to the server"),
    ({"STUB_WAL": "none", "STUB_PG_READY": "2"}, "the database copy is not running"),
])
def test_the_copys_link(dog, stub, said):
    dog.run(T0, **stub)
    [(_, text)] = dog.run(T0 + 300, **stub)
    assert text.startswith("⚠️ The live copy of the database on this Mac has lost the server:") and said in text


def test_the_copys_files_and_dumps(dog):
    dog.run(T0, sync=4000, dump=9000)
    sent = dict((t.split(":")[0], t) for _c, t in dog.run(T0 + 300, sync=4300, dump=9300))
    assert "last full sync 1 h 11 min ago" in sent["⚠️ The server's files aren't being copied to this Mac"]
    assert "the last one is 2 h 35 min old" in sent["⚠️ The hourly database dump on this Mac isn't being made"]


def test_the_disk(dog):
    dog.run(T0, DISK_MIN_GB=10**9)
    [(_, text)] = dog.run(T0 + 300, DISK_MIN_GB=10**9)
    assert text.startswith("⚠️ This Mac's disk is filling up:") and text.endswith("GB left.")


def test_a_mac_that_slept_says_so_when_telegram_is_back(dog):
    dog.run(T0)
    assert dog.run(T0 + 3 * 3600, STUB_TG="down") == []              # woke, the network not back yet
    [(_, text)] = dog.run(T0 + 3 * 3600 + 300)
    assert text.startswith("ℹ️ This Mac checked nothing from ") and "(asleep or off)" in text
    assert dog.run(T0 + 3 * 3600 + 600) == []


def test_an_alert_that_reached_nobody_is_tried_again(dog):
    dog.run(T0, STUB_PROD="down", STUB_TG="down")
    assert dog.run(T0 + 300, STUB_PROD="down", STUB_TG="down") == []
    [(_, text)] = dog.run(T0 + 600, STUB_PROD="down")
    assert "isn't answering" in text and not text.startswith("(still")


def test_the_chat_the_user_linked_and_kept_when_the_copy_is_down(dog):
    dog.env.pop("ALERT_CHAT_ID")
    dog.env["ALERT_USERNAME"] = "masein"
    dog.run(T0, STUB_PROD="down", STUB_CHAT="4242")
    [(chat, _)] = dog.run(T0 + 300, STUB_PROD="down", STUB_CHAT="4242")
    assert chat == "4242"
    dog.run(T0 + 600, STUB_WAL="none", STUB_PG_READY="2")             # the copy down: no lookup
    [(chat2, text)] = dog.run(T0 + 900, STUB_WAL="none", STUB_PG_READY="2")
    assert chat2 == "4242" and "the database copy is not running" in text


def test_the_token_never_on_a_command_line(dog):
    dog.run(T0, STUB_PROD="down")
    dog.run(T0 + 300, STUB_PROD="down")
    assert "SECRET" not in dog.argv_file.read_text()
    assert "bot123456:SECRET-token/sendMessage" in dog.config_file.read_text()


def test_in_persian(dog):
    dog.run(T0, STUB_PROD="down", ALERT_LANG="fa")
    [(_, text)] = dog.run(T0 + 300, STUB_PROD="down", ALERT_LANG="fa")
    assert text.startswith("⚠️ سایت حسابداری جواب نمی‌دهد")
    [(_, back)] = dog.run(T0 + 600, ALERT_LANG="fa")
    assert back.startswith("✅ درست شد: سایت حسابداری")


def test_status_and_test(dog):
    assert dog.run(T0, "--status", STUB_PROD="down") == []
    assert "prod" in dog.stdout and "Failed to connect" in dog.stdout and "relay       ok" in dog.stdout
    assert "Telegram chat 777" in dog.stdout
    [(chat, text)] = dog.run(T0, "--test")
    assert chat == "777" and text.startswith("✅ Test message")
