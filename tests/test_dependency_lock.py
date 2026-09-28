"""Locked, hash-checked dependencies (roadmap 2026-09 §2.7).

The image and CI install ``requirements*.lock`` with ``--require-hashes``;
``requirements*.txt`` keep the ranges and ``scripts/lock-deps.sh`` re-pins.
These checks fail when a lock no longer matches its ranges, when a pin has no
hash, or when the test locks drift from what the image ships — and they keep
the offline deploy from shipping the source tree or a database dump."""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]
LOCKS = {"requirements.txt": "requirements.lock", "requirements-dev.txt": "requirements-dev.lock",
         "requirements-e2e.txt": "requirements-e2e.lock"}


def _ranges(name: str) -> dict[str, Requirement]:
    """Every requirement a .txt asks for, following -r includes."""
    out: dict[str, Requirement] = {}
    for raw in (ROOT / name).read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("-r "):
            out.update(_ranges(line[3:].strip()))
            continue
        req = Requirement(line)
        out[canonicalize_name(req.name)] = req
    return out


def _pins(name: str) -> dict[str, tuple[str, int]]:
    """name → (version, number of hashes) from a pip-compile --generate-hashes lock."""
    text = (ROOT / name).read_text(encoding="utf-8")
    pins: dict[str, tuple[str, int]] = {}
    for block in re.split(r"\n(?=[A-Za-z0-9])", text):
        m = re.match(r"([A-Za-z0-9_.\-]+)(?:\[[^\]]*\])?==([^\s\\;]+)", block)
        if m:
            pins[canonicalize_name(m.group(1))] = (m.group(2), len(re.findall(r"--hash=sha256:[0-9a-f]{64}", block)))
    return pins


@pytest.mark.parametrize("txt,lock", sorted(LOCKS.items()))
def test_each_lock_pins_every_range_within_it(txt, lock):
    pins = _pins(lock)
    for name, req in _ranges(txt).items():
        assert name in pins, f"{lock} has no pin for {name} — run scripts/lock-deps.sh"
        version = pins[name][0]
        assert req.specifier.contains(version, prereleases=True), \
            f"{lock} pins {name}=={version}, outside {txt}'s {req.specifier} — run scripts/lock-deps.sh"


@pytest.mark.parametrize("lock", sorted(LOCKS.values()))
def test_every_pin_carries_hashes(lock):
    pins = _pins(lock)
    assert len(pins) > 20
    unhashed = [n for n, (_v, hashes) in pins.items() if not hashes]
    assert not unhashed, f"{lock}: no hash for {unhashed}"


@pytest.mark.parametrize("lock", ["requirements-dev.lock", "requirements-e2e.lock"])
def test_the_test_locks_run_what_the_image_ships(lock):
    shipped = {n: v for n, (v, _h) in _pins("requirements.lock").items()}
    tested = {n: v for n, (v, _h) in _pins(lock).items()}
    drift = {n: (shipped[n], tested[n]) for n in shipped if n in tested and shipped[n] != tested[n]}
    assert not drift, f"{lock} pins differently from requirements.lock: {drift}"
    assert set(shipped) <= set(tested)


def test_pypdf_may_move_past_4():
    assert _ranges("requirements.txt")["pypdf"].specifier.contains("6.0.0")


def test_the_image_and_ci_install_the_locks_with_hashes():
    docker = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "pip install --no-cache-dir --require-hashes -r /app/requirements.lock" in docker
    assert "requirements.txt" not in docker.split("COPY . /app")[0].replace("# requirements.txt keeps", "")
    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert ci.count("pip install --require-hashes -r requirements-dev.lock") == 2
    assert "pip install --require-hashes -r requirements-e2e.lock" in ci
    assert "pip install -r requirements" not in ci


def test_no_dump_or_backup_enters_the_image():
    ignored = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    for pattern in ("*.dump", "*.sql", "*.sql.gz", "*.tgz", "backups/", ".env", "app/uploads/"):
        assert pattern in ignored, pattern


def test_the_offline_deploy_ships_the_production_compose_not_the_source():
    script = (ROOT / "scripts/offline-deploy.sh").read_text(encoding="utf-8")
    assert "docker compose -f docker-compose.prod.yml up -d --pull never" in script
    assert "docker compose up -d" not in script                    # the dev stack
    assert not re.search(r"tar [^\n]*\s\.\s*$", script, re.M)       # no archive of the whole tree
    ship = re.search(r"SHIP=\((.*?)\)", script, re.S).group(1).split()
    assert "docker-compose.prod.yml" in ship and not any(s in (".", "app", "tests") for s in ship)
    for pattern in ("'*.dump'", "'*.sql'", "'backups'", "'.env'"):
        assert pattern in script, pattern
    for path in ship:
        assert (ROOT / path).exists(), path
