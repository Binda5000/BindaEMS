#!/usr/bin/env python3
"""Startet den BindaEMS-Watchdog: ``bindaems-watchdog.py <config.ini>`` (siehe README.md)."""

from __future__ import annotations

import os
import sys

# Das Paket ``bindaems_watchdog`` liegt neben diesem Skript.
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

from bindaems_watchdog.service import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
