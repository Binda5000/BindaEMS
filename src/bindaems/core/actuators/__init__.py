"""Konkrete Aktoren: EVCS und Victron schreiben per MQTT über den Cerbo, der Tesla über Tessie."""

from typing import Final

# Gründe, die EVCS und Victron teilen, weil beide über denselben Cerbo schreiben (Plan „Texte“)
REASON_NOT_CONNECTED: Final = "Cerbo nicht verbunden."
REASON_NOT_ACCEPTED: Final = "Cerbo-Verbindung: Auftrag nicht angenommen."
