"""Docker-HEALTHCHECK: fragt ``/v1/health`` der internen API ab (0 = gesund, 1 = nicht)."""

from __future__ import annotations

import os
import sys

import httpx

TIMEOUT_S = 3.0


def main() -> int:
    token = os.environ.get("BINDAEMS_INTERNAL_TOKEN", "")
    port = os.environ.get("BINDAEMS_CORE_PORT", "8081")
    try:
        response = httpx.get(
            f"http://127.0.0.1:{port}/v1/health",
            headers={"Authorization": f"Bearer {token}"},
            timeout=TIMEOUT_S,
            trust_env=False,  # nie über einen Proxy
        )
    except httpx.HTTPError:
        return 1
    return 0 if response.status_code == 200 else 1


if __name__ == "__main__":
    sys.exit(main())
