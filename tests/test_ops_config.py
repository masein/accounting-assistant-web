"""Operational safety (security review 2026-09-24 H5 + M3): images publish
only after CI passes, the deployer is pinned, backups exist, the session cookie
is Secure and HSTS is on in production. Deploys come from two registries, the
first that answers (2026-11-16, scenarios CJ1–CJ5). Only a commit CI passed on
main becomes `latest`, every workflow job has a timeout, and the public
repository names no server (review 2026-11-28, scenarios CW3–CW8)."""
from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_publish_workflow_waits_for_a_green_ci_run():
    wf = yaml.safe_load((ROOT / ".github/workflows/publish-image.yml").read_text())
    on = wf.get("on") or wf.get(True)  # PyYAML parses a bare `on:` key as boolean True
    assert "workflow_run" in on and on["workflow_run"]["workflows"] == ["CI"]
    assert on["workflow_run"]["types"] == ["completed"] and on["workflow_run"]["branches"] == ["main"]
    assert "branches" not in on.get("push", {}), "a push to main must not publish directly"
    job = wf["jobs"]["build-push"]
    assert "workflow_run.conclusion == 'success'" in job["if"]
    # `branches: [main]` matches a fork PR's head branch called main as well:
    # only a push to this repository may publish (review 2026-11-16)
    assert "workflow_run.event == 'push'" in job["if"]
    assert "workflow_run.head_repository.full_name == github.repository" in job["if"]
    checkout = next(s for s in job["steps"] if str(s.get("uses", "")).startswith("actions/checkout"))
    assert "workflow_run.head_sha" in checkout["with"]["ref"]


def test_prod_compose_is_hardened_and_backs_up():
    c = yaml.safe_load((ROOT / "docker-compose.prod.yml").read_text())
    svc = c["services"]
    assert "watchtower" not in svc
    d = svc["deployer"]
    assert d["image"].startswith("docker:") and d["image"].count(".") >= 2      # an exact version: it holds the socket
    assert "/var/run/docker.sock:/var/run/docker.sock" in d["volumes"]
    assert d["entrypoint"] == ["sh", "scripts/deploy.sh"] and d["command"] == ["loop"]
    # CW8: the default is GHCR alone, so this public file names no other server; a server that also deploys from
    # the mirror lists it in its .env (DEPLOY.md §5)
    assert d["environment"]["DEPLOY_REGISTRIES"] == "${DEPLOY_REGISTRIES-ghcr.io/masein/accounting-assistant-api}"
    # the stack's folder at the same path inside, so compose there is the same project
    assert d["working_dir"] == "${DEPLOY_DIR:-/opt/accounting}" and ".:${DEPLOY_DIR:-/opt/accounting}" in d["volumes"]
    api = svc["api"]
    assert api["image"] == "accounting-assistant-api:deployed" and api["pull_policy"] == "never"
    assert api["environment"]["AUTH_COOKIE_SECURE"] == "${AUTH_COOKIE_SECURE:-true}"
    assert "--proxy-headers" in api["command"] and "--forwarded-allow-ips" in api["command"]
    assert api["ports"] == ["${API_BIND:-0.0.0.0}:${API_PORT:-8000}:8000"]
    for name in ("db", "api", "deployer", "backup"):
        assert svc[name]["logging"]["options"]["max-size"] == "10m", name
    b = svc["backup"]
    assert b["image"].startswith("postgres:")
    assert any(v.endswith(":/backups") for v in b["volumes"])
    assert any(v == "uploads:/uploads:ro" for v in b["volumes"])
    # period-close snapshots live on their own volume and are in the nightly backup: in the container's own
    # layer every redeploy deleted them (2026-11-16)
    assert "private_uploads:/app/app/private_uploads" in api["volumes"] and "private_uploads" in c["volumes"]
    assert "private_uploads:/private_uploads:ro" in b["volumes"]
    from app.api.exports import SNAPSHOT_DIR
    assert SNAPSHOT_DIR.parent == ROOT / "app" / "private_uploads"
    loop = (ROOT / "scripts/backup-loop.sh").read_text(encoding="utf-8")
    assert '-C "$PRIVATE_UPLOADS" .' in loop and "-name 'private-uploads-*.tgz'" in loop
    assert 'PRIVATE_UPLOADS="${BACKUP_PRIVATE_UPLOADS:-/private_uploads}"' in loop
    assert "/app/app/private_uploads/snapshots" in (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert '"${3:-}"' in (ROOT / "scripts/restore.sh").read_text(encoding="utf-8")
    assert b["depends_on"]["db"]["condition"] == "service_healthy"


def test_backup_scripts_are_executable_and_parse():
    for name in ("backup-loop.sh", "backup.sh", "restore.sh", "deploy.sh"):
        p = ROOT / "scripts" / name
        assert p.exists() and (p.stat().st_mode & 0o111), name
        shell = "sh" if name in ("backup-loop.sh", "deploy.sh") else "bash"
        subprocess.run([shell, "-n", str(p)], check=True)
    assert "pg_dump -Fc" in (ROOT / "scripts/backup-loop.sh").read_text()
    assert "pg_restore" in (ROOT / "scripts/restore.sh").read_text()
    assert "backups/" in (ROOT / ".gitignore").read_text()
    env = (ROOT / ".env.prod.example").read_text()
    for key in ("AUTH_COOKIE_SECURE=", "FORWARDED_ALLOW_IPS=", "API_BIND=", "BACKUP_DIR=", "BACKUP_KEEP_DAYS="):
        assert key in env, key


def test_hsts_only_in_production(client, monkeypatch):
    from app.core.config import settings
    assert "strict-transport-security" not in client.get("/health").headers
    monkeypatch.setattr(settings, "app_env", "prod")
    h = client.get("/health").headers
    assert h.get("strict-transport-security", "").startswith("max-age=31536000")


def test_deploy_script_tries_each_registry_and_rolls_back():
    """CJ1–CJ4: the script the deployer runs — every registry in order, the first that answers; a registry that
    failed skipped a while; the api recreated on a new image only; the previous image back when it isn't healthy;
    a bad image not tried again. Run against a real registry by hand (DEPLOY.md §5); here its shape."""
    script = (ROOT / "scripts/deploy.sh").read_text(encoding="utf-8")
    assert "for repo in $REGISTRIES" in script and 'timeout "$PULL_TIMEOUT" docker pull' in script
    assert "RETRY_AFTER" in script and "failed-" in script
    assert 'docker tag "$LOCAL:deployed" "$LOCAL:previous"' in script
    assert 'docker tag "$LOCAL:previous" "$LOCAL:deployed"' in script and 'echo "$got" > "$STATE/bad"' in script
    assert 'docker compose up -d --no-deps --force-recreate "$SERVICE"' in script
    assert "same_project" in script                                  # never a second stack beside the real one
    env = (ROOT / ".env.prod.example").read_text(encoding="utf-8")
    for key in ("DEPLOY_REGISTRIES=", "DEPLOY_TAG=", "DEPLOY_DIR=", "COMPOSE_FILE=docker-compose.prod.yml"):
        assert key in env, key
    assert "API_IMAGE=" not in env and "WATCHTOWER" not in env
    assert ".deploy/" in (ROOT / ".gitignore").read_text(encoding="utf-8")


def test_the_live_copy_key_can_only_read(tmp_path):
    """DEPLOY.md §13: the live copy's SSH key runs only ops/live-copy/server/accounting-copy-gate.sh. Anything but a
    database pipe, a folder check and rsync SENDING one of the three synced folders is refused."""
    import os
    gate = ROOT / "ops/live-copy/server/accounting-copy-gate.sh"

    def run(cmd):
        env = {**os.environ, "SSH_ORIGINAL_COMMAND": cmd, "SSH_CLIENT": "203.0.113.9 50000 22"}
        return subprocess.run(["sh", str(gate)], env=env, capture_output=True, text=True, timeout=10).returncode

    for refused in ("id", "sh -c id", "bash", "", "has /etc", "has ../../etc",
                    "rsync --server -logDtpre.iLsfxCIvu . /opt/accounting/backups/",          # receiving: a write
                    "rsync --server --sender -logDtpre.iLsfxCIvu . /etc/",
                    "rsync --server --sender -logDtpre.iLsfxCIvu . /opt/accounting/",
                    "rsync --server --sender --files-from=/etc/shadow . /opt/accounting/backups/",
                    "rsync --server --sender -e/bin/sh . /opt/accounting/backups/",
                    "rsync --server --sender -logDtpre.iLsfxCIvu /etc/passwd /opt/accounting/backups/",
                    "db-pipe; id", "has uploads; id"):
        assert run(refused) != 0, refused
    pipe = (ROOT / "ops/live-copy/scripts/pg-pipe.sh").read_text(encoding="utf-8")
    assert pipe.rstrip().endswith('"$REMOTE" db-pipe')
    deploy = (ROOT / "DEPLOY.md").read_text(encoding="utf-8")
    assert 'restrict,command="/usr/local/bin/accounting-copy-gate"' in deploy
    assert {".env", "known_hosts", "data/"} <= set((ROOT / "ops/live-copy/.gitignore").read_text().split())


def _workflow(name):
    wf = yaml.safe_load((ROOT / ".github/workflows" / name).read_text(encoding="utf-8"))
    wf["on"] = wf.get("on") or wf.get(True)
    return wf


def test_only_a_commit_ci_passed_on_main_becomes_latest():
    """CW3: `latest` is what the deployer ships to production within minutes. A manual run on main publishes it only
    for a commit whose CI passed (looked up first; the run fails otherwise), a manual run elsewhere publishes only
    sha-<short>, and a version tag (which skips CI) never moves it."""
    wf = _workflow("publish-image.yml")
    assert "workflow_dispatch" in wf["on"]
    job = wf["jobs"]["build-push"]
    assert job["permissions"]["actions"] == "read"
    gate = next(s for s in job["steps"] if "green CI run" in s.get("name", ""))
    assert gate["if"] == "github.event_name == 'workflow_dispatch' && github.ref == 'refs/heads/main'"
    assert "actions/workflows/ci.yml/runs?head_sha=$SHA&branch=main&event=push&status=success" in gate["run"]
    assert "exit 1" in gate["run"]
    names = [s.get("name", "") for s in job["steps"]]
    assert names.index(gate["name"]) < names.index("Build & push to GHCR")
    meta = next(s for s in job["steps"] if str(s.get("uses", "")).startswith("docker/metadata-action"))
    assert meta["with"]["flavor"].strip() == "latest=false"           # no `latest` from a semver tag on its own
    [latest] = [t for t in meta["with"]["tags"].splitlines() if t.strip().startswith("type=raw,value=latest")]
    assert "github.event_name == 'workflow_run'" in latest
    assert "github.event_name == 'workflow_dispatch' && github.ref == 'refs/heads/main'" in latest
    assert "!= 'push'" not in latest
    header = (ROOT / ".github/workflows/publish-image.yml").read_text(encoding="utf-8").split("\non:", 1)[0]
    assert "(and `latest`)" not in header and "never `latest`" in header


def test_the_mirror_host_comes_from_a_repository_variable():
    """CW8: no registry host in the workflows; with MIRROR_REGISTRY unset the mirror steps and the sync are skipped."""
    for name in ("publish-image.yml", "mirror-sync.yml"):
        wf = _workflow(name)
        assert wf["env"]["MIRROR_REGISTRY"] == "${{ vars.MIRROR_REGISTRY }}", name
        assert "vars.MIRROR_REGISTRY" in wf["env"]["MIRROR_IMAGE"], name
    for step in _workflow("publish-image.yml")["jobs"]["build-push"]["steps"]:
        if "mirror" in step.get("name", "").lower():
            assert "vars.MIRROR_REGISTRY != ''" in step["if"], step["name"]
    assert "vars.MIRROR_REGISTRY != ''" in _workflow("mirror-sync.yml")["jobs"]["sync"]["if"]
    assert "Watchtower" not in (ROOT / ".github/workflows/mirror-sync.yml").read_text(encoding="utf-8")


def test_the_ai_eval_without_a_key_is_skipped_not_green():
    """CW4: the eval job is gated on a small job that reads the secret, so without AI_EVAL_API_KEY it shows as
    skipped with a notice, instead of a green "AI eval" that ran nothing."""
    wf = _workflow("ai-eval.yml")
    gate, ev = wf["jobs"]["key"], wf["jobs"]["eval"]
    assert gate["outputs"]["run"] == "${{ steps.key.outputs.run }}"
    [step] = gate["steps"]
    assert step["env"]["KEY"] == "${{ secrets.AI_EVAL_API_KEY }}" and "::notice" in step["run"]
    assert ev["needs"] == "key" and ev["if"] == "needs.key.outputs.run == 'true'"
    assert not any("steps.key" in str(s.get("if", "")) for s in ev["steps"])
    assert "17 scenarios" not in (ROOT / ".github/workflows/ai-eval.yml").read_text(encoding="utf-8")


# every job's name as branch protection knows it: renaming one would leave a required check that never reports
JOB_NAMES = {
    "ci.yml": {"test": "Tests (pytest)", "lint": "Lint (ruff) — informational", "postgres": "Tests on PostgreSQL",
               "e2e": "Browser smoke (Playwright)"},
    "publish-image.yml": {"build-push": "Build & push API image"},
    "mirror-sync.yml": {"sync": "Copy GHCR latest to the mirror when it is behind"},
    "ai-eval.yml": {"key": "Eval key", "eval": "AI eval"},
    "android.yml": {"app": "Android app"},
    "apple.yml": {"app": "Apple app"},
}


def test_every_workflow_job_has_a_timeout_and_keeps_its_name():
    """CW5: a hung job fails within its timeout instead of holding a runner for six hours; job names unchanged."""
    files = sorted(p.name for p in (ROOT / ".github/workflows").glob("*.yml"))
    assert files == sorted(JOB_NAMES)
    for name in files:
        jobs = _workflow(name)["jobs"]
        assert {k: j["name"] for k, j in jobs.items()} == JOB_NAMES[name], name
        for key, job in jobs.items():
            assert 0 < int(job["timeout-minutes"]) <= 60, (name, key)
    run = next(s["run"] for s in _workflow("ci.yml")["jobs"]["postgres"]["steps"]
               if s.get("name") == "Run the suite against PostgreSQL")
    assert "--maxfail=25" in run and " -x " not in run
    prod = (ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")
    assert "its lifespan" not in prod and "entrypoint" in prod.split("services:", 1)[0]


# What a public repository must not say about the servers it runs on (review 2026-11-28): a private network
# address, a home folder on someone's Mac (the placeholder /Users/owner/ aside), root's SSH folder — and the owner's domain and the mirror's CDN, matched
# by the SHA-256 of the word so this test doesn't name them either.
INFRA = re.compile(r"/Users/(?!owner/)\w|/root/\.ssh|\b(?:10\.\d+|172\.(?:1[6-9]|2\d|3[01])|192\.168)\.\d+\.\d+\b")
INFRA_WORDS = {"9b23161827c6c9238551eeacd576f22dee7dbf913c8082a39917e64277da07a7",
               "9c66dd6cf2e5c1dd32f42e68a9b6fb54f85eb9f53c5bc134a668869545504cca"}
SCANNED = ["DEPLOY.md", ".env.example", ".env.prod.example", "docker-compose.prod.yml", "docker-compose.yml",
           ".github", "docs", "scripts", "ops/live-copy", "ops/watchdog", "tests/test_mail_service.py",
           "tests/test_watchdog.py", "tests/test_ops_scripts.py", "tests/test_chat_sessions_intake.py"]


def _says_infra(line):
    if INFRA.search(line):
        return True
    words = {w.lower() for w in re.findall(r"[A-Za-z0-9]{6,}", line)}
    return any(hashlib.sha256(w.encode()).hexdigest() in INFRA_WORDS for w in words)


def test_no_server_names_addresses_or_local_paths_in_the_public_repo():
    """CW8: production's and the mirror's hostnames, the server's addresses, the owner's local paths. Each value a
    deployment needs is read from its configuration (a repository variable, .env, watchdog.env). The relay
    (ops/telegram-relay, DEPLOY.md §14) is scrubbed separately."""
    found = []
    for entry in SCANNED:
        base = ROOT / entry
        for path in ([base] if base.is_file() else sorted(p for p in base.rglob("*") if p.is_file())):
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            if path.name == "DEPLOY.md":
                start, end = text.find("## 14. "), text.find("## 15. ")
                if 0 <= start < end:
                    text = text[:start] + text[end:]
            for n, line in enumerate(text.splitlines(), 1):
                if _says_infra(line):
                    found.append(f"{path.relative_to(ROOT)}:{n}: {line.strip()[:120]}")
    assert found == []


def test_gitignore_keeps_the_ops_secrets_out_and_the_examples_in():
    """CW7: the relay's token, the watchdog's settings and state, the live copy's .env; examples stay committed."""
    lines = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    for entry in ("ops/telegram-relay/token.env", "ops/watchdog/watchdog.env", "ops/watchdog/state/",
                  "ops/live-copy/.env", ".env", ".env.*", "!.env*.example"):
        assert entry in lines, entry
    assert lines.index("!.env*.example") > lines.index(".env.*")       # a negation only works after what it undoes
