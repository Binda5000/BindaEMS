---
title: "HA-Dashboard von evcc auf BindaEMS: Entity-IDs und Karten"
author: Claude
date: 2026-10-19
branch: main
cost: { minutes: 6, tokens: 156191 }
touches:
  - src/bindaems/app/ha/entities.py
---

## Decided
- Binda's HA-Dashboard (Mushroom + Bubble-Card-Pop-ups #egolf, #ladestation, #dodo) auf BindaEMS umgebaut; Ergebnis liegt nur im Projektordner /mnt/project-files/dashboard/ (heute = 3c/4a, zweite Fassung für 4b), nicht im Repo.
- Entity-IDs folgen aus der Discovery (src/bindaems/app/ha/entities.py `discovery`): `has_entity_name` ohne `object_id`, also `<component>.bindaems_<Gerätename-Slug>_<Entitätsname-Slug>`, z. B. select.bindaems_e_golf_lademodus, button.bindaems_tesla_model_3_boost, sensor.bindaems_evcs_ladeleistung, switch.bindaems_regelbetrieb.
- Für die Familie ist Boost („Jetzt voll laden“, bis zum Abstecken, max. 12 h) der Startknopf, nicht „Sofort“ (bleibt dauerhaft).
- Keine neuen HACS-Karten nötig: mushroom-select-card / mushroom-number-card, Aktionen per `perform-action: button.press`.

## Tried and ruled out
- Wallbox des Autos über den Wallbox-Namen auf die Ladeleistung abbilden: eigene Namen aus dem UI (wallbox_names) brechen das; stattdessen die Wallbox suchen, deren Sensor „Fahrzeug“ gleich dem Gerätenamen ohne „BindaEMS “ ist (device_attr).
- Uhrzeit-Picker aus HACS für die Abfahrtszeit: gibt es nur für input_datetime, nicht für MQTT-`text`.

## Watch out
- An der Anlage bestätigt (Screenshots 19.10.): Fahrzeuge „e-Golf“, „Tesla Model 3“; Wallbox-Namen im UI „Einfahrt“ (EVCS), „Garage“ (Wall Connector).
- sensor.bindaems_<auto>_wallbox ist die zugeordnete Wallbox (Health vehicles[].wallbox), auch ohne Stecker; „angesteckt“ nur aus sensor.bindaems_<wallbox>_fahrzeug ableiten.
- Bubble-Card-Pop-ups legen Karten ohne grid_options halb breit; jeder Karte columns 12 geben.
- Ziel-SOC, Abfahrtszeit, Fertig um, Ziel erreichbar und der Modus „Geplant“ gibt es erst ab 4b (fb23bd0 an der Anlage hat sie nicht).
- HA kennt keinen Sensor „Boost läuft“; nur an der Ladeleistung sichtbar.
