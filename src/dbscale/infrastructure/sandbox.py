"""Sandbox providers: where the isolated synthetic database lives.

Lifecycle: ``start() -> url`` ... ``destroy()``. The default provider runs a
disposable Docker container; ``ExternalSandbox`` lets users point DBScale at
a database they manage themselves (CI services, remote hosts).
"""

from __future__ import annotations

import secrets
import shutil
import subprocess
import time
from abc import ABC, abstractmethod

from dbscale.core.config import SandboxConfig


class SandboxError(RuntimeError):
    pass


class Sandbox(ABC):
    description: str = "sandbox"

    @abstractmethod
    def start(self) -> str:
        """Create/prepare the sandbox and return a connection URL."""

    @abstractmethod
    def destroy(self) -> None: ...

    @property
    def keep(self) -> bool:
        return False


class ExternalSandbox(Sandbox):
    """A database the user already runs. DBScale creates/drops tables inside it."""

    def __init__(self, url: str):
        self.url = url
        self.description = "external database"

    def start(self) -> str:
        return self.url

    def destroy(self) -> None:  # nothing to tear down; tables are dropped by the runner if requested
        return None

    @property
    def keep(self) -> bool:
        return True


class DockerPostgresSandbox(Sandbox):
    """Disposable ``postgres`` container on a random host port."""

    def __init__(
        self,
        image: str = "postgres:16-alpine",
        name: str | None = None,
        keep: bool = False,
        password: str | None = None,
        startup_timeout_s: float = 90.0,
    ):
        self.image = image
        self.name = name or f"dbscale-sandbox-{secrets.token_hex(4)}"
        self._keep = keep
        self.password = password or secrets.token_urlsafe(12)
        self.startup_timeout_s = startup_timeout_s
        self.container_id: str | None = None
        self.port: int | None = None
        self.description = f"docker {image}"

    @property
    def keep(self) -> bool:
        return self._keep

    @property
    def url(self) -> str:
        if self.port is None:
            raise SandboxError("Sandbox not started")
        return f"postgresql://postgres:{self.password}@127.0.0.1:{self.port}/dbscale"

    def start(self) -> str:
        if shutil.which("docker") is None:
            raise SandboxError("Docker CLI not found. Install Docker or use sandbox.type: url")
        self._ensure_image()
        cmd = [
            "docker",
            "run",
            "--detach",
            "--name",
            self.name,
            "--publish",
            "127.0.0.1::5432",
            "--env",
            f"POSTGRES_PASSWORD={self.password}",
            "--env",
            "POSTGRES_DB=dbscale",
            "--label",
            "dbscale=sandbox",
            "--tmpfs",
            "/var/run/postgresql",
            self.image,
            "-c",
            "fsync=off",
            "-c",
            "full_page_writes=off",
            "-c",
            "synchronous_commit=off",
            # Autovacuum during a multi-chunk load competes for disk with the insert.
            # The runner ANALYZEs after the load, which is the only statistics the benchmark needs.
            "-c",
            "autovacuum=off",
            "-c",
            "checkpoint_timeout=30min",
            "-c",
            "max_wal_size=4GB",
            # Left small on purpose: benchmark numbers are relative across scales, not absolute.
            "-c",
            "shared_buffers=256MB",
        ]
        if not self._keep:
            cmd.insert(2, "--rm")
        result = _docker(cmd)
        self.container_id = result.strip()
        self.port = self._mapped_port()
        self._wait_ready()
        return self.url

    def destroy(self) -> None:
        if self.container_id is None:
            return
        subprocess.run(
            ["docker", "rm", "--force", "--volumes", self.container_id],
            capture_output=True,
            text=True,
            check=False,
        )
        self.container_id = None

    # --------------------------------------------------------------- helpers

    def _ensure_image(self) -> None:
        check = subprocess.run(["docker", "image", "inspect", self.image], capture_output=True, text=True)
        if check.returncode != 0:
            _docker(["docker", "pull", self.image])

    def _mapped_port(self) -> int:
        out = _docker(["docker", "port", self.name, "5432/tcp"])
        # "127.0.0.1:55001" possibly multiple lines (ipv4/ipv6)
        for line in out.splitlines():
            if ":" in line:
                try:
                    return int(line.rsplit(":", 1)[1])
                except ValueError:
                    continue
        raise SandboxError(f"Could not determine mapped port for container {self.name}: {out!r}")

    def _wait_ready(self) -> None:
        import psycopg

        deadline = time.monotonic() + self.startup_timeout_s
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                with psycopg.connect(self.url, connect_timeout=3) as conn:
                    with conn.cursor() as cur:
                        cur.execute("SELECT 1")
                return
            except Exception as exc:  # noqa: BLE001 - container is still booting
                last_error = exc
                time.sleep(0.5)
        self.destroy()
        raise SandboxError(
            f"Sandbox database did not become ready in {self.startup_timeout_s}s: {last_error}"
        )


def _docker(cmd: list[str]) -> str:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise SandboxError(f"{' '.join(cmd[:3])} failed: {proc.stderr.strip() or proc.stdout.strip()}")
    return proc.stdout


def create_sandbox(config: SandboxConfig) -> Sandbox:
    if config.type == "url":
        if not config.url:
            raise SandboxError("sandbox.type is 'url' but sandbox.url is not set")
        return ExternalSandbox(config.url)
    return DockerPostgresSandbox(image=config.image, name=config.name, keep=config.keep)
