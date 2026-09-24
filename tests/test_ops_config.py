"""Operational safety (security review 2026-09-24 H5 + M3): images publish
only after CI passes, Watchtower is pinned, backups exist, the session cookie
is Secure and HSTS is on in production."""
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
    checkout = next(s for s in job["steps"] if str(s.get("uses", "")).startswith("actions/checkout"))
    assert "workflow_run.head_sha" in checkout["with"]["ref"]


def test_prod_compose_is_hardened_and_backs_up():
    c = yaml.safe_load((ROOT / "docker-compose.prod.yml").read_text())
    svc = c["services"]
    assert svc["watchtower"]["image"] != "containrrr/watchtower" and ":" in svc["watchtower"]["image"]
    api = svc["api"]
    assert api["environment"]["AUTH_COOKIE_SECURE"] == "${AUTH_COOKIE_SECURE:-true}"
    assert "--proxy-headers" in api["command"] and "--forwarded-allow-ips" in api["command"]
    assert api["ports"] == ["${API_BIND:-0.0.0.0}:${API_PORT:-8000}:8000"]
    for name in ("db", "api", "watchtower", "backup"):
        assert svc[name]["logging"]["options"]["max-size"] == "10m", name
    b = svc["backup"]
    assert b["image"].startswith("postgres:")
    assert any(v.endswith(":/backups") for v in b["volumes"])
    assert any(v == "uploads:/uploads:ro" for v in b["volumes"])
    assert b["depends_on"]["db"]["condition"] == "service_healthy"


def test_backup_scripts_are_executable_and_parse():
    for name in ("backup-loop.sh", "backup.sh", "restore.sh"):
        p = ROOT / "scripts" / name
        assert p.exists() and (p.stat().st_mode & 0o111), name
        shell = "sh" if name == "backup-loop.sh" else "bash"
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
