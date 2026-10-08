"""Startet den core als eigenen Prozess und beendet ihn mit SIGTERM."""

import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import yaml


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def test_sigterm_shuts_down_cleanly(tmp_path: Path) -> None:
    raw = yaml.safe_load(Path("deploy/config.example.yaml").read_text(encoding="utf-8"))
    port = _free_port()
    raw["telemetry"]["spool_dir"] = str(tmp_path / "spool")
    raw["core_api"] = {"host": "127.0.0.1", "port": port}
    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump(raw), encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("BINDAEMS_")}
    env["BINDAEMS_INTERNAL_TOKEN"] = "x" * 32
    proc = subprocess.Popen(  # noqa: S603 - fester Aufruf des eigenen Pakets
        [sys.executable, "-m", "bindaems.core", "--config", str(config)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 20
        while True:  # warten, bis die interne API lauscht
            assert proc.poll() is None, proc.stderr.read().decode() if proc.stderr else ""
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                break
            except OSError:
                assert time.monotonic() < deadline, "core startet nicht"
                time.sleep(0.1)
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=20) == 0
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
