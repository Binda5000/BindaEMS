"""Gemeinsame Test-Helfer für ems-app (nur für Tests)."""

from __future__ import annotations

from datetime import UTC, datetime

from argon2 import PasswordHasher

T_APP = datetime(2026, 10, 9, 8, 0, tzinfo=UTC)  # 10:00 Ortszeit

PASSWORD = "richtig-geheim-1"
# billige Parameter, damit die Tests schnell bleiben; der Dienst nutzt sonst die Standardwerte
FAST_HASHER = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)
