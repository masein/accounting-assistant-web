"""Operational safety (security review 2026-09-24 H5 + M3): images publish
only after CI passes, the deployer is pinned, backups exist, the session cookie
is Secure and HSTS is on in production. Deploys come from two registries, the
first that answers (2026-11-16, scenarios CJ1–CJ5)."""
from __future__ import annotations

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
    regs = d["environment"]["DEPLOY_REGISTRIES"]
    assert "ghcr.io/masein/accounting-assistant-api" in regs and "docker.netixsystem.com/accounting-assistant-api" in regs
    assert regs.index("ghcr.io") < regs.index("docker.netixsystem.com")         # GHCR first, the mirror second
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
    assert "-C /private_uploads ." in loop and "-name 'private-uploads-*.tgz'" in loop
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
