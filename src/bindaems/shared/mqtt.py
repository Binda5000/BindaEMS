"""TLS für MQTT-Verbindungen (Cerbo im core, Mosquitto von Home Assistant in der app)."""

from __future__ import annotations

import ssl
from pathlib import Path
from typing import Protocol


class TlsSettings(Protocol):
    @property
    def tls(self) -> bool: ...

    @property
    def tls_verify(self) -> bool: ...

    @property
    def tls_ca_file(self) -> Path | None: ...


def build_tls_context(cfg: TlsSettings) -> ssl.SSLContext | None:
    """TLS-Kontext; ``tls_verify=False`` akzeptiert selbstsignierte Zertifikate (z. B. Cerbo)."""
    if not cfg.tls:
        return None
    cafile = str(cfg.tls_ca_file) if cfg.tls_ca_file is not None else None
    context = ssl.create_default_context(cafile=cafile)
    if not cfg.tls_verify:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    return context
