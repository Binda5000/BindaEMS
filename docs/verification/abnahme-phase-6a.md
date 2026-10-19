# Abnahme Phase 6a – Checkliste

Spec Abschnitt 18, Phase 6, „Abnahme 6a“: Alle Werte der Empfangstabelle kommen an; der Versatz fällt
5 min nach dem letzten Paket auf 0 (an der Anlage beobachtet); 7 Tage Dry-Run ohne Fehler; im
Live-Betrieb liegt der Versatz nie außerhalb ±3 K, ändert sich weg von 0 höchstens um eine Stufe je
10 min und steht spätestens einen Zyklus nach einem Wechsel der Betriebsart oder nach veralteten
Paketen auf 0; jeder gesendete Versatz ist durch das Echo bestätigt.

Diese Checkliste füllst du an der Anlage aus. Kopiere sie dafür nach
`docs/verification/JJJJ-MM-TT-abnahme-phase-6a.md`, trage die Ergebnisse ein und checke sie ein.
Voraussetzung: Plan 6a ist ausgerollt (nach der Abnahme der Phase 3, Betriebshandbuch, Abschnitt
„Klimaanlage (Phase 6a)“), das Netz für UDP 5442 zwischen 192.168.81.0/24 und 192.168.82.0/24 ist
freigegeben und das Prüfprotokoll Teil 2, Punkt `thermal` (Spec 17.2-9), liegt als Bericht vor. Die
Abschnitte 1 und 2 prüfst du vor dem Dry-Run, Abschnitt 3 im Dry-Run und Abschnitt 4 nach der
Live-Schaltung.

Nichts davon schaltet von selbst: Der Aktor `thermal.<name>` startet im Dry-Run und sendet dort nur 0.
`coe_probe listen` sendet nie; `verify_part2 --punkt thermal` sendet nur auf ausdrücklichen Aufruf und
nach Bestätigung.

| Angabe | Wert |
|---|---|
| Anlage | |
| Version core / app (UI → System) | |
| Name der Klimaanlage (Schlüssel unter `thermal.devices`) | |
| C.M.I. (`cmi_host`) / BindaEMS (VM) | 192.168.81.92 / 192.168.82.20 |
| Ausgerollt am | |
| Bericht Prüfprotokoll Teil 2, Punkt `thermal` | |
| Dry-Run von – bis (mindestens 7 Tage) | |
| Live seit | |
| Prüfzeitraum Live von – bis | |
| Prüfer | |

---

## 1. Alle Werte der Empfangstabelle kommen an

**Fundort:** `coe_probe listen` (core gestoppt), danach UI → System → „Klimaanlage“ und
`GET /v1/health`, `thermal.<name>`.

Prüfschritte:

1. Den core stoppen und mithören (Betriebshandbuch, „Empfang prüfen“); die Probe sendet nichts:

   ```bash
   cd /opt/bindaems
   docker compose stop ems-core
   docker compose run --rm --service-ports --entrypoint python ems-core \
     -m bindaems.tools.coe_probe listen --sekunden 120
   docker compose up -d ems-core
   ```

2. Jeder Wert der Tabelle erscheint mindestens einmal, der Absender ist `192.168.81.92`, und die
   Knotennummer ist die in `receive` (56). Trage Einheit und Rohwert der Zeile ein.
3. Der Einheiten-Code der Leistung (Analog 4) ist bestätigt und `unit` und `scale` in `config.yaml`
   passen: `Rohwert / scale` ergibt die Leistung in kW.
4. Mit laufendem core zeigt die Karte „Klimaanlage“ „Werte kommen an“ und alle Zeilen haben einen
   Wert; `GET /v1/health` hat in `thermal.<name>` keinen `null` bei `actual_c`, `setpoint_c`,
   `offset_active_k`, `power_w`, `mode` und `running`. „Fremde Pakete“ und „Fehlerhafte Pakete“
   fehlen oder bleiben konstant.

Nachweis: Ausgabe der Probe (Auszug) und Bildschirmfoto der Karte.

| Wert | Ausgang in `receive` | Einheit | Rohwert (Beispiel) | Wert in Health | angekommen |
|---|---|---|---|---|---|
| Ist (`actual_c`) | Knoten 56, Analog 1 | 1 (°C) | | | ☐ |
| Soll inklusive Versatz (`setpoint_c`) | Knoten 56, Analog 2 | 1 (°C) | | | ☐ |
| Echo des Versatzes (`offset_active_k`) | Knoten 56, Analog 3 | 1 | | | ☐ |
| Leistung (`power_kw`) | Knoten 56, Analog 4 | 10, `scale` ____ | | | ☐ |
| Befehl Ein (`cmd_on`) | Knoten 56, Digital 1 | – | | | ☐ |
| Kühlen (`cooling`) | Knoten 56, Digital 2 | – | | | ☐ |
| Lüften (`venting`) | Knoten 56, Digital 3 | – | | | ☐ |
| Heizen (`heating`) | Knoten 56, Digital 4 | – | | | ☐ |
| Läuft (`running`) | Knoten 56, Digital 5 | – | | | ☐ |

Hinweis: Bei Abweichungen von der Tabelle des Betreibers (Stand 18.10.2026) trägst du die Werte der
Anlage ein und passt `config.yaml` an.

Datum: ________ Ergebnis: ☐ bestanden ☐ nicht bestanden Unterschrift: ________

---

## 2. Timeout der UVR: Versatz fällt nach 5 min ohne Paket auf 0

**Fundort:** Prüfprotokoll Teil 2, Punkt `thermal`, Schritt „Timeout der UVR“; die Visu der UVR.

Prüfschritte:

1. Den Punkt ohne `--yes` ausführen (Betriebshandbuch, „Prüfprotokoll Teil 2, Punkt thermal“); der
   core ist dabei gestoppt. Die Zeile „Timeout der UVR“ im Bericht ist OK: Das Werkzeug sendet +0,5 K,
   wartet auf das Echo, sendet dann bis zu 5 min 30 s nichts, und das Echo fällt von selbst auf 0.
2. Zusätzlich die Visu der UVR beobachten: Der Versatz „EMS“ steht ab dem letzten Paket 5 min später
   wieder auf 0. Notiere die Uhrzeiten.
3. Die Zeilen „Versatz +0,5 K“ (Echo binnen 60 s, Visu zeigt „EMS“ +0,5 K) und „Versatz 0“ sind OK;
   am Ende steht „Am Ende: Versatz 0“ auf OK.

Nachweis: Bericht des Prüfprotokolls (`JJJJ-MM-TT-pruefprotokoll-teil2-thermal.md`).

| Messung | Wert |
|---|---|
| Uhrzeit des letzten gesendeten Pakets (+0,5 K) | |
| Uhrzeit, ab der das Echo 0 zeigt | |
| Dauer ohne Paket bis Echo 0 (erwartet rund 5 min, höchstens 5 min 30 s) | |
| Visu der UVR zeigt danach 0 | ☐ |

Datum: ________ Ergebnis: ☐ bestanden ☐ nicht bestanden Unterschrift: ________

---

## 3. 7 Tage Dry-Run ohne Fehler

**Fundort im UI:** System → „Klimaanlage“ und „Aktoren“, Entscheidungen, Alarme; Grafana, Panel
„Klimaanlage“.

Der Aktor bleibt im Dry-Run (Betriebshandbuch, „Dry-Run beobachten“): Der Adapter sendet alle 30 s 0,
das Echo bleibt 0, und das Entscheidungslog zeigt, welchen Versatz der core im Live-Betrieb senden
würde. „Ohne Fehler“ heißt:

- Kein Alarm `control.thermal_exception` und kein Alarm `thermal.echo_mismatch.<name>`.
- Die Karte „Klimaanlage“ zeigt die Werte ohne Unterbrechung (kein „Keine Werte“ über mehr als
  `stale_after_s`, außer bei einem bekannten Ausfall des C.M.I., der in der Tabelle steht und danach
  von selbst endet).
- Das Echo bleibt 0, der Versatz an der UVR bleibt 0 (Grafana, `offset_k` und `offset_sent_k`).
- Keine Entscheidung an die Klimaanlage mit dem Ergebnis `error`, `timeout` oder `rejected`.
- Jeder Wunsch hat einen Grund aus der Liste des Betriebshandbuchs („Gründe“), und die Gründe passen
  zu dem, was an dem Tag geschah.

Prüfschritte:

1. Jeden Tag die Karte „Klimaanlage“ und die Alarme unter System → „core“ ansehen und in der Tabelle
   abhaken.
2. Am Ende des Zeitraums die Entscheidungen auswerten (`SINCE` ist der Beginn des Dry-Runs in UTC,
   z. B. `2026-10-20T00:00:00%2B00:00`; Zugang wie im Betriebshandbuch, Abschnitt 4, „Diagnose“):

   ```bash
   curl -s -H "Authorization: Bearer $BINDAEMS_INTERNAL_TOKEN" \
     "http://127.0.0.1:8081/v1/decisions?since=$SINCE&limit=5000" \
     | jq -r '.decisions[] | select(.actuator == "thermal.klima")
         | [.ts, .dry_run, .result, .requested, (.codes | join(","))] | @tsv'
   ```

   Zähle die Zeilen je Ergebnis und Grund; `result` darf nur `dry_run` sein. `more` in der Antwort
   sagt, ob weitere Entscheidungen folgen (Betriebshandbuch, Abschnitt 13, „Entscheidungslog“).
3. In Grafana (InfluxDB, Messung `thermal`) zeigt das Panel „Klimaanlage“ für den Zeitraum Ist, Soll,
   das Echo und den gesendeten Versatz. Der gesendete Versatz bleibt 0:

   ```sql
   SELECT MAX("offset_sent_k"), MIN("offset_sent_k"), MAX("offset_k"), MIN("offset_k")
   FROM "raw"."thermal" WHERE time >= '2026-10-20T00:00:00Z' AND time < '2026-10-27T00:00:00Z'
   ```

   Alle vier Werte sind 0. (`offset_sent_k` wird nur bei Änderung geschrieben; ohne Änderung kann die
   Abfrage leer sein.)
4. Der Dry-Run lief mit Regelbetrieb an, und die Anlage kühlte oder heizte im Zeitraum. Lief sie nie,
   sagt der Dry-Run nichts über die Strategie; verlängere ihn dann.

| Tag | Karte „Werte kommen an“ | kein Alarm | Echo 0 | Entscheidungen (Zahl) | Gründe plausibel | Bemerkung |
|---|---|---|---|---|---|---|
| 1 | ☐ | ☐ | ☐ | | ☐ | |
| 2 | ☐ | ☐ | ☐ | | ☐ | |
| 3 | ☐ | ☐ | ☐ | | ☐ | |
| 4 | ☐ | ☐ | ☐ | | ☐ | |
| 5 | ☐ | ☐ | ☐ | | ☐ | |
| 6 | ☐ | ☐ | ☐ | | ☐ | |
| 7 | ☐ | ☐ | ☐ | | ☐ | |

Ausfälle des C.M.I. im Zeitraum (Beginn – Ende, Ursache, erwartet `thermal_stale` und Versatz 0):

| Beginn | Ende | Ursache | Entscheidung `thermal_stale` | Versatz 0 |
|---|---|---|---|---|
| | | | ☐ | ☐ |

Datum: ________ Ergebnis: ☐ bestanden ☐ nicht bestanden Unterschrift: ________

---

## 4. Im Live-Betrieb: Grenzen, Stufen, Rückfall und Bestätigung

**Fundort im UI:** System → „Aktoren“ und „Klimaanlage“, Entscheidungen; Grafana, Panel
„Klimaanlage“.

Voraussetzung: Abschnitte 1 bis 3 sind bestanden, und die Klimaanlage ist live geschaltet
(Betriebshandbuch, „Live schalten“). Wähle einen Zeitraum, in dem die Anlage kühlt oder heizt und
der PV-Überschuss in den Versatz geht (mindestens 7 Tage; trage den Zeitraum oben ein). Auswertung
aus dem Entscheidungslog (alle gesendeten, also nicht im Dry-Run entschiedenen Befehle) und aus
InfluxDB. `SINCE` ist der Beginn des Live-Zeitraums in UTC:

```bash
curl -s -H "Authorization: Bearer $BINDAEMS_INTERNAL_TOKEN" \
  "http://127.0.0.1:8081/v1/decisions?since=$SINCE&limit=5000" > klima.json
```

**4.1 Der Versatz liegt nie außerhalb ±3 K.**

```bash
jq '[.decisions[] | select(.actuator == "thermal.klima" and (.dry_run | not)) | .applied]
    | [min, max]' klima.json
```

Beide Werte liegen in −3 … +3. Dazu in InfluxDB (alle vier Werte liegen in −3 … +3; `offset_k` ist
das Echo der UVR, also das, was die Anlage wirklich anwendet):

```sql
SELECT MAX("offset_sent_k"), MIN("offset_sent_k"), MAX("offset_k"), MIN("offset_k")
FROM "raw"."thermal" WHERE time >= '2026-10-20T00:00:00Z' AND time < '2026-10-27T00:00:00Z'
```

**4.2 Weg von 0 höchstens eine Stufe je 10 min.** Diese Abfrage listet jede Entscheidung, die den
Betrag des Versatzes gegenüber der vorherigen erhöht, mit Zeit, altem und neuem Wert und dem Abstand
in Sekunden. Jede Zeile hat Stufe 0,5 K (neuer Betrag minus alter) und einen Abstand von mindestens
600 s (die Zeit einer Entscheidung ist die der Bestätigung durch das Echo und schwankt um 1 bis 2 s;
Abstände ab 598 s sind in Ordnung). Ein Vorzeichenwechsel geht nur über 0:

```bash
jq -r 'def secs: sub("\\.[0-9]+"; "") | sub("\\+00:00$"; "Z") | fromdateiso8601;
  [.decisions[] | select(.actuator == "thermal.klima" and (.dry_run | not))]
  | sort_by(.ts) as $d | range(1; $d | length) as $i
  | select(($d[$i].requested | fabs) > ($d[$i - 1].requested | fabs))
  | [$d[$i].ts, $d[$i - 1].requested, $d[$i].requested,
     (($d[$i].ts | secs) - ($d[$i - 1].ts | secs))] | @tsv' klima.json
```

**4.3 Spätestens einen Zyklus nach einem Wechsel der Betriebsart oder nach veralteten Paketen steht der
Versatz auf 0.** Suche die Entscheidungen mit den Gründen `thermal_mode` und `thermal_stale`:

```bash
jq -r '.decisions[] | select(.actuator == "thermal.klima" and (.dry_run | not)
    and any(.codes[]; . == "thermal_mode" or . == "thermal_stale"))
    | [.ts, .requested, .applied, .result, (.codes | join(","))] | @tsv' klima.json
```

- **Wechsel der Betriebsart:** Der Verlauf von „Klima Betriebsart“ in Home Assistant (oder das Panel
  „Klimaanlage“) nennt die Zeitpunkte, zu denen die Anlage zwischen Kühlen, Heizen, Lüften und Aus
  wechselte. Zu jedem Wechsel, bei dem der Versatz nicht schon 0 war, gibt es eine Entscheidung
  `thermal_mode` (bei Lüften und Aus `thermal_off`) mit dem Versatz 0, innerhalb von 2 s nach dem Paket
  mit der neuen Betriebsart (ein Zyklus des core ist 1 s; das C.M.I. sendet bei Änderung sofort).
  Das Echo steht danach auf 0.
- **Veraltete Pakete:** Nach `stale_after_s` (180 s) ohne Paket, bei einem Versatz ungleich 0, gibt es
  eine Entscheidung `thermal_stale` mit dem Versatz 0, binnen 190 s nach dem letzten Paket (die
  Überwachung des Adapters läuft im 10-s-Takt). Danach sendet der Adapter nur 0. Trage Ausfälle des
  C.M.I. mit Beginn und Ende ein.

**4.4 Jeder gesendete Versatz ist durch das Echo bestätigt.**

```bash
jq -r '[.decisions[] | select(.actuator == "thermal.klima" and (.dry_run | not))]
    | group_by(.result) | map({result: .[0].result, n: length})' klima.json
```

Alle Entscheidungen haben `result` `ok` („gesendet und bestätigt“). Es gibt keine mit `error`,
`timeout` oder `not_sent`, keinen Alarm `thermal.echo_mismatch.<name>` und keinen Alarm
`actuator.failed.thermal.<name>`. Stichprobe: Mindestens drei Versätze vergleichst du mit der Visu der
UVR („EMS“) und mit dem Echo im Panel (`offset_k`).

**4.5 Weiteres.**

- Kein Alarm `control.thermal_exception` im Zeitraum.
- Das Panel „Klimaanlage“ zeigt, dass die Einspeisung in den Stunden, in denen der Versatz „mehr“
  war, sank, und dass bei einem ladenden Auto (`thermal_cars`) der Versatz zurückging.
- Notiere den Komfort: Die Raumtemperatur blieb in dem Bereich, den der Betreiber erwartet.

Nachweis: Ausgaben der Abfragen (Auszug) und Bildschirmfotos des Panels.

| Kriterium | Ergebnis |
|---|---|
| 4.1 Versatz kleinster / größter Wert (K) | von ____ bis ____ ☐ innerhalb ±3 K |
| 4.2 Erhöhungen des Betrags geprüft (Zahl) | ____ ☐ alle 0,5 K ☐ alle mit Abstand ≥ 600 s |
| 4.3 Wechsel der Betriebsart (Zahl) / davon mit Versatz 0 im selben Zyklus | ____ / ____ ☐ |
| 4.3 Ausfälle des C.M.I. (Zahl) / davon mit Versatz 0 nach `thermal_stale` | ____ / ____ ☐ |
| 4.4 Entscheidungen live (Zahl) / davon `ok` | ____ / ____ ☐ alle bestätigt |
| 4.5 Alarme im Zeitraum | ☐ keine |

Datum: ________ Ergebnis: ☐ bestanden ☐ nicht bestanden Unterschrift: ________

---

## Gesamtergebnis

| Abschnitt | Ergebnis |
|---|---|
| 1. Alle Werte der Empfangstabelle kommen an | ☐ bestanden |
| 2. Timeout der UVR: Versatz fällt nach 5 min ohne Paket auf 0 | ☐ bestanden |
| 3. 7 Tage Dry-Run ohne Fehler | ☐ bestanden |
| 4. Im Live-Betrieb: Grenzen, Stufen, Rückfall und Bestätigung | ☐ bestanden |

Die Phase 6a ist abgenommen, wenn alle vier Abschnitte bestanden sind.

Datum: ________ Unterschrift: ________
