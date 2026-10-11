"""Atomare JSON-Dateien des core.

Das ist die einzige Stelle, an der ``core.safety`` und ``core.control`` Dateien anfassen.
Geschrieben wird über eine temporäre Datei im selben Verzeichnis und ``os.replace``: Ein Leser
sieht immer den alten oder den neuen Inhalt, nie einen halb geschriebenen, auch wenn der Prozess
oder der Rechner mitten im Schreiben ausfällt.
"""

from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
from typing import Any


def write_json_atomic(path: Path, data: Any) -> None:
    """Schreibt ``data`` als JSON (UTF-8, Umlaute bleiben lesbar) nach ``path``.

    Die Datei ist danach ganz neu oder unverändert. Das Verzeichnis wird bei Bedarf angelegt. Der
    Inhalt geht zuerst nach ``<name>.tmp`` im selben Verzeichnis (``os.replace`` ist nur innerhalb
    eines Dateisystems atomar), wird mit ``flush`` und ``os.fsync`` auf die Platte gebracht und
    ersetzt dann die Zieldatei.

    Bei einem Fehler bleibt die alte Datei, die temporäre Datei wird entfernt, und der Fehler
    wird weitergegeben: ``OSError`` bei der Platte, ``TypeError`` oder ``ValueError`` bei Daten,
    die sich nicht als JSON schreiben lassen. Je Datei schreibt nur ein Schreiber zur Zeit; die
    temporäre Datei hat einen festen Namen.
    """
    text = json.dumps(data, ensure_ascii=False)  # Daten, die nicht gehen, berühren die Platte nicht
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{path.name}.tmp")
    try:
        with temp.open("w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    except BaseException:
        with contextlib.suppress(OSError):  # fehlt sie oder geht es nicht, zählt der erste Fehler
            temp.unlink()
        raise


def read_json(path: Path) -> Any:
    """Liest eine JSON-Datei (UTF-8).

    - ``FileNotFoundError``, wenn die Datei fehlt
    - ``ValueError``, wenn der Inhalt kein JSON ist: leer, abgeschnitten, falsche Kodierung oder
      zu tief verschachtelt
    - ``OSError``, wenn sich die Datei sonst nicht lesen lässt (Rechte, Verzeichnis)
    """
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except RecursionError as exc:
        raise ValueError(f"JSON zu tief verschachtelt: {path}") from exc
