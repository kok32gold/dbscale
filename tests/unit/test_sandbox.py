"""Sandbox lifecycle without starting Docker."""

import shutil
import subprocess

import pytest

from dbscale.core.config import SandboxConfig
from dbscale.infrastructure.sandbox import (
    DockerPostgresSandbox,
    ExternalSandbox,
    SandboxError,
    create_sandbox,
)


def test_external_sandbox_returns_url_and_destroy_is_a_noop():
    sandbox = create_sandbox(SandboxConfig(type="url", url="postgresql://localhost/db"))
    assert isinstance(sandbox, ExternalSandbox)
    assert sandbox.start() == "postgresql://localhost/db"
    sandbox.destroy()
    assert sandbox.keep is True


def test_url_sandbox_without_url_fails_before_any_container():
    with pytest.raises(SandboxError, match="sandbox.url"):
        create_sandbox(SandboxConfig(type="url"))


def test_docker_cli_missing(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    with pytest.raises(SandboxError, match="Docker CLI not found"):
        DockerPostgresSandbox().start()


def test_docker_run_failure_does_not_include_the_password(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/docker")

    def fake_run(cmd, **_kwargs):
        class Proc:
            returncode = 1
            stderr = "Cannot connect to the Docker daemon"
            stdout = ""

        return Proc()

    monkeypatch.setattr(subprocess, "run", fake_run)
    sandbox = DockerPostgresSandbox(password="sandbox-secret")
    with pytest.raises(SandboxError, match="Docker daemon") as exc:
        sandbox.start()
    assert "sandbox-secret" not in str(exc.value)
    assert "POSTGRES_PASSWORD" not in str(exc.value)


def test_unreadable_port_mapping_is_an_error(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/docker")
    calls = {"n": 0}

    def fake_run(cmd, **_kwargs):
        class Proc:
            def __init__(self, code, out="", err=""):
                self.returncode = code
                self.stdout = out
                self.stderr = err

        calls["n"] += 1
        if cmd[:3] == ["docker", "image", "inspect"]:
            return Proc(0)
        if cmd[:3] == ["docker", "run", "--detach"] or cmd[:3] == ["docker", "run", "--rm"]:
            return Proc(0, out="container-id\n")
        if cmd[:3] == ["docker", "port", "dbscale-test"]:
            return Proc(0, out="not-a-port\n")
        return Proc(1, err=f"unexpected {cmd}")

    monkeypatch.setattr(subprocess, "run", fake_run)
    sandbox = DockerPostgresSandbox(name="dbscale-test", password="pw")
    with pytest.raises(SandboxError, match="mapped port"):
        sandbox.start()


def test_url_before_start_is_refused():
    sandbox = DockerPostgresSandbox(password="sandbox-secret")
    with pytest.raises(SandboxError, match="not started"):
        _ = sandbox.url
    assert "sandbox-secret" not in sandbox.description
