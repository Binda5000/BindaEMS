---
title: HA-Benachrichtigungen per Blueprint ohne Codeänderung
author: Claude
date: 2026-10-20
branch: claude/ha-benachrichtigungen-f1jd8u
cost: { minutes: 12, tokens: 165778 }
---

## Decided
- Push-Meldungen (Laden, Warnungen) laufen als HA-Blueprint, nicht im Repo: /mnt/project-files/ha-benachrichtigungen/ (bindaems_benachrichtigungen.yaml, LIESMICH.md). Die vorhandenen MQTT-Entitäten reichen.
- Laden: Auslöser sind sensor.bindaems_<wallbox>_ladeleistung (numeric_state mit `for`) und _fahrzeug (von/zu „keins“). Energie, Herkunft und Kosten kommen aus den Attributen von sensor.bindaems_<fahrzeug-slug>_ladeenergie.
- Warnungen: persistent_notification-Trigger auf die Kennungen `bindaems_*`. Beim Entfernen bekommt das Handy clear_notification mit Tag = notification_id.
- Geräte: mobile_app-Mehrfachauswahl, der Dienst ist `notify.mobile_app_{{ device_attr(id,'name')|slugify }}` (ein name_by_user stört nicht).

## Tried and ruled out
- Die Bilanz direkt beim Ende der Ladung senden: Die app bucht Session-Energie nur je Viertelstunde (Stats-Refresh je Slot). Darum wartet der Blueprint bis zum Slotende plus 3 min.
- Die Leerzeichen am Anfang von HA-Template-Variablen gehen verloren (HA strippt das Ergebnis). Text deshalb mit `teile|select|join(' ')` bauen.

## Watch out
- Ein Neustart der app (nicht verfügbar → 0 W) darf kein „beendet“ melden. Darum gilt die Bedingung trigger.from_state numerisch.
- Ein gesetztes `homeassistant.notify_service` in config.yaml bedeutet doppelte Warnungen auf diesem Handy.
- Test mit pytest-homeassistant-custom-component: hass.async_block_till_done hängt, solange eine delay läuft. Mit freezer.tick + async_fire_time_changed + asyncio.sleep(0) ticken.
