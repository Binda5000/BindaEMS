# Abnahme Phase 5c – Checkliste (Vorheizen und Vorkühlen)

Plan 5c, Entscheidung 16 (der dritte Teil der Phase 5, Spec 18): Die Abnahme hat zwei Teile.

- **Teil A (Vorschau, 14 Tage, Schalter aus):** Das Heizmodell taugt, die Vorschau steht in jedem Plan
  mit Ersparnis und ohne Fehler, und die Karte „Wärmemodell“ zeigt τ und `C` plausibel.
- **Teil B (14 Tage live, nach deiner Entscheidung: Aktor live, Schalter an):**
  1. Ist minus Soll ohne Versatz liegt in mindestens 95 % der Viertelstunden im Komfortband ±0,5 K,
  2. der Versatz bleibt in ±3 K und folgt der Schrittregel (wie 6a),
  3. jeder positive Planwert, während der Akku entlud, ging binnen 6 min eine Stufe zurück,
  4. die geschätzte Ersparnis ist nicht negativ,
  5. die Räume in Home Assistant bleiben beim Vorheizen höchstens 0,5 K über dem eingestellten Band
     (Sichtprüfung, kein Code),
  6. und du hast nichts Unangenehmes gemerkt.

Diese Checkliste füllst du an der Anlage aus. Kopiere sie dafür nach
`docs/verification/JJJJ-MM-TT-abnahme-phase-5c.md`, trage die Ergebnisse ein und checke sie ein.
Voraussetzung: Plan 5c ist ausgerollt, frühestens nach den Abnahmen 5a und 5b am 22.11.2026
(Betriebshandbuch, Abschnitt „Vorheizen und Vorkühlen (Phase 5c)“, „Vorheizen und Vorkühlen ausrollen
(Plan 5c)“). Teil A beginnt, sobald das Heizmodell taugt (Abschnitt A2); dafür braucht es 14 Tage mit
der Betriebsart Heizen in den letzten 28 Tagen. Teil B beginnt erst nach Teil A, nach der Abnahme 6a
(`docs/verification/abnahme-phase-6a.md`, der Aktor `thermal.<name>` ist live) und wenn du dich dafür
entscheidest.

**Nichts geht von selbst live:** Der Schalter „Vorheizen und Vorkühlen“ ist ab Werk aus; der Plan
rechnet die Verschiebung dann nur als Vorschau, an den core geht sie nicht. Selbst eingeschaltet wirkt
sie erst, wenn der Aktor live ist: Im Dry-Run sendet der core weiter 0 und schreibt den Wunsch ins
Entscheidungslog. Gelernte Werte erreichen die Safety-Schicht nie und ändern keine harte Grenze
(Spec 10.5); das Komfortband liegt immer innerhalb von `max_offset_k`.

Abfragen der app laufen mit Anmeldung (`jar` und `$APP` wie im Betriebshandbuch, Abschnitt 8,
„Alternative für Skripte (curl)“); das Entscheidungslog des core liest du über die interne API
(Abschnitt 4, „Diagnose“, Port 8081 kurzzeitig veröffentlichen). Abfragen auf InfluxDB laufen in
Grafana (Explore, InfluxQL, Datenbank `bindaems`) oder mit dem `influx`-CLI. Zeiten in API, Datenbank
und InfluxDB sind UTC; die Seiten zeigen Ortszeit. Die Beispiele nehmen den Schlüssel `klima` der
Beispielkonfiguration; bei einem anderen Gerät setzt du dessen Schlüssel ein.

| Angabe | Wert |
|---|---|
| Anlage | |
| Version core / app (UI → System) | |
| Name der Klimaanlage (erstes Gerät der Art `coe_offset` unter `thermal.devices`) | |
| Ausgerollt am | |
| Heizmodell taugt seit (Karte „Wärmemodell“) | |
| Teil A von – bis (mindestens 14 Tage, Schalter aus) | |
| Abnahme 6a bestanden am / Aktor `thermal.<name>` live seit | |
| Schalter „Vorheizen und Vorkühlen“ an seit | |
| Teil B von – bis (mindestens 14 Tage) | |
| Komfortband Heizen laut Einstellungen (`heat_up_k` / `heat_down_k`) | |
| `max_offset_k` der Klimaanlage und wirksames Band (je Richtung der kleinere Wert, auf 0,5 K abgerundet) | |
| Prüfer | |

---

## Teil A – Vorschau mit Schalter aus (14 Tage)

### A1. Start: Migration, Komponente und Anzeige

**Fundort im UI:** System → „Komponenten“, `thermal_model`; Prognosen → Karte „Wärmemodell“; Zeitplan →
Karte „Vorheizen und Vorkühlen“.

Prüfschritte:

1. Nach dem Start ist die Migration `0015` gelaufen, und die app berechnet das Wärmemodell gleich
   (Nachtlauf sonst um 03:50 Uhr). Die Komponente `thermal_model` meldet „Wärmemodell aktuell“ oder,
   bis es Daten gibt, „Noch keine Daten der Klimaanlage“; „Wärmemodell fehlgeschlagen: {Fehler}“ ist
   ein Befund:

   ```bash
   cd /opt/bindaems
   docker compose logs ems-app | grep -i "Wärmemodell"
   ```

   „Wärmemodell berechnet“ steht nach jedem Lauf im Log.
2. Die Karte „Wärmemodell“ zeigt je Betriebsart „Heizen“ und „Kühlen“ den Kopf mit der Zahl der Tage,
   die Zeilen „Grundleistung“, „Zeitkonstante“, „Speicher“, „Auskühl-Phasen“ und „Anpassung“ und den
   Zustand („Modell taugt.“ oder den Grund), darunter „Berechnet am {TT.MM. HH:MM}.“.
3. Der Schalter „Vorheizen und Vorkühlen“ ist aus (**Einstellungen** → „Vorheizen und Vorkühlen“, in
   Home Assistant der Schalter `thermal_plan`).

| Prüfpunkt | Beobachtet |
|---|---|
| Komponente `thermal_model` (Text) | |
| erste Berechnung am | |
| Karte „Wärmemodell“ mit beiden Betriebsarten | ☐ |
| Schalter „Vorheizen und Vorkühlen“ aus | ☐ |

Datum: ________ Ergebnis: ☐ bestanden ☐ nicht bestanden Unterschrift: ________

### A2. Das Heizmodell taugt

**Fundort im UI:** Prognosen → Karte „Wärmemodell“, Betriebsart „Heizen“.

Ein Modell taugt mit mindestens 14 Tagen mit der Betriebsart in den letzten 28 Tagen, mindestens 3
Auskühl-Phasen, `R²` der Grundleistung von mindestens 0,5 und einer Zeitkonstante zwischen 4 und
200 h. Die Karte zeigt dann „Modell taugt.“, sonst den ersten Grund, der fehlt (etwa „Noch zu wenig
Tage mit Heizen: {n} von 14.“).

Prüfschritte:

1. Die Zahlen aus der API abrufen (`GET /api/learning/thermal`, ab „Lesen“):

   ```bash
   curl -s -b jar "$APP/api/learning/thermal" \
     | jq '.modes[] | {mode, usable, reason, text, computed_at, days: .model.days,
         u_w_per_k: .model.u_w_per_k, r2: .model.r2, tau_h: .model.tau_h,
         episodes: .model.episodes, c_kwh_per_k: .model.c_kwh_per_k}'
   ```

2. Für `heizen` ist `usable` wahr und `reason` `ok`, die Karte zeigt „Modell taugt.“, und das bleibt
   über die 14 Tage von Teil A so (jede Nacht ein neuer Stand). Fällt das Modell zwischendurch auf
   einen Grund zurück (etwa `poor_fit`), notierst du Tag und Grund; die Vorschau rechnet dann nicht,
   und Teil A beginnt neu, sobald das Modell wieder taugt.
3. Die Wärme des Ölofens in den Bädern ist nicht gemessen. Sie verlängert dort das Auskühlen; ob das
   Heizmodell trotzdem trägt, zeigen `R²`, τ und die Auskühl-Phasen. Ein `R²` knapp über 0,5 ist
   bestanden, aber notiere es.

| Tag | `usable` | `reason` | Tage | `R²` | τ (h) | Auskühl-Phasen | Bemerkung |
|---|---|---|---|---|---|---|---|
| Beginn Teil A | ☐ | | | | | | |
| Mitte | ☐ | | | | | | |
| Ende Teil A | ☐ | | | | | | |

Datum: ________ Ergebnis: ☐ bestanden ☐ nicht bestanden Unterschrift: ________

### A3. Die Vorschau steht in jedem Plan, mit Ersparnis und ohne Fehler

**Fundort im UI:** Zeitplan → Karte „Vorheizen und Vorkühlen“ (unter „Ablauf“).

Mit Schalter aus rechnet jeder Plan die Verschiebung mit. Lohnt sie sich (mindestens 2 ct über den
Plan), steht über der Tabelle „Vorschau: Vorheizen/Vorkühlen würde etwa {x} ct sparen.“, darunter die
Spalte „Klima“ mit dem Versatz je Zeitraum (etwa „+1,0 K“, „−0,5 K“, „–“); im Plan ist `thermal.reason`
dann `ok`, `active` falsch und `saving_ct` mindestens 2. Gibt es nichts als Vorschau zu zeigen (es lohnt
sich nicht, die Anlage heizt gerade nicht oder die Rechnung scheiterte), steht dort „Vorheizen und
Vorkühlen ist aus.“ und im Plan `reason` `off`. **Mit Schalter aus unterscheidet der Plan diese Fälle
nicht**; ob eine Rechnung scheiterte, sagt das Log der app (Schritt 3).

Prüfschritte:

1. Stichprobe an mindestens drei Tagen: Die Karte „Vorheizen und Vorkühlen“ steht im Zeitplan, und
   die Antwort der API (`GET /api/plan`, ab „Lesen“) passt zur Karte:

   ```bash
   curl -s -b jar "$APP/api/plan" | jq '.plan.thermal'
   ```

   Bei `reason` `ok` zeigt die Spalte „Klima“ Zeiträume mit Versatz; vorgeheizt wird in günstigen
   oder PV-reichen Stunden, abgesenkt in teuren. Die Sätze `KLIMA_MEHR` und `KLIMA_WENIGER` stehen im
   Ablauf **nicht**, solange der Schalter aus ist.
2. Alle Pläne des Zeitraums zählen (nur lesend, `SINCE` ist der Beginn von Teil A, UTC; die Pläne
   bleiben 30 Tage):

   ```bash
   cd /opt/bindaems
   SINCE=2026-12-01
   docker compose exec ems-app python -c "import sqlite3; db = sqlite3.connect('file:/data/bindaems.sqlite3?mode=ro', uri=True); [print(r) for r in db.execute(\"select json_extract(data, '$.thermal.mode'), json_extract(data, '$.thermal.reason'), json_extract(data, '$.thermal.active'), count(*), min(json_extract(data, '$.thermal.saving_ct')), max(json_extract(data, '$.thermal.saving_ct')), sum(json_extract(data, '$.thermal.model_computed_at') is null) from plan where created_at >= '$SINCE' group by 1, 2, 3 order by 1, 2, 3\")]"
   ```

   Je Zeile: Betriebsart, Grund, aktiv, Zahl der Pläne, kleinste und größte Ersparnis in ct, Zahl der
   Pläne ohne Stand des Modells. Bestanden, wenn
   - **kein** Plan ohne `thermal` dasteht (eine Zeile mit `None` als Betriebsart heißt: keine
     Klimaanlage im Plan),
   - `active` überall `0` (falsch) ist,
   - jede Zeile mit `heizen` und `ok` eine kleinste Ersparnis von mindestens 2,0 ct hat,
   - und die Zeilen mit `heizen` den Stand des Modells tragen (letzte Spalte 0).
   Zeilen mit `lueften`, `aus` oder `unbekannt` stehen mit `off` da; das ist kein Befund. Wie oft
   `heizen` mit `ok` und wie oft mit `off` vorkommt, trägst du ein: Mit dem großen Hausakku ist `off`
   (es lohnt sich nicht) oft zu erwarten (Betriebshandbuch, „Was Vorheizen und Vorkühlen an dieser
   Anlage bringt“).
3. **Ohne Fehler:** Im Log der app steht im Zeitraum weder „Vorheizen und Vorkühlen nicht berechnet“
   (die Rechnung warf) noch „Verschiebung der Klimaanlage verworfen, weil sie unplausibel ist“ (die
   Rechnung plante einen Versatz außerhalb des Bands):

   ```bash
   docker compose logs --since "$SINCE" ems-app \
     | grep -E "Vorheizen und Vorkühlen nicht berechnet|Verschiebung der Klimaanlage verworfen"
   ```

   Die Ausgabe ist leer. Das Log reicht nur bis zum letzten Neuanlegen des Containers zurück (etwa
   durch ein Update); wurde `ems-app` im Zeitraum neu angelegt, notierst du die Lücke und prüfst ab
   dann jeden Tag (`--since 24h`). Die Komponente `planning` blieb in Ordnung, und kein Plan wurde
   wegen der Verschiebung zum Ersatzplan: Die Verschiebung kann einen Plan nie verhindern. Ein
   Zeitlimit des Solvers (5 s) schreibt keine Zeile ins Log; es zeigt sich erst mit Schalter an als
   Grund `solver` (Teil B, „Weiteres“).

| Betriebsart | Grund | Pläne (Zahl) | Ersparnis kleinste / größte (ct) | ohne Stand des Modells |
|---|---|---|---|---|
| `heizen` | `ok` | | | |
| `heizen` | `off` | | | |
| andere | `off` | | | |

| Prüfpunkt | Ergebnis |
|---|---|
| jeder Plan mit `thermal` | ☐ |
| `active` nie wahr | ☐ |
| jede Vorschau (`ok`) mit mindestens 2 ct | ☐ |
| Log ohne die beiden Zeilen | ☐ |
| Stichproben Karte und API passen (Tage) | ☐ |

Datum: ________ Ergebnis: ☐ bestanden ☐ nicht bestanden Unterschrift: ________

### A4. τ und `C` sind plausibel

**Fundort im UI:** Prognosen → Karte „Wärmemodell“, „Zeitkonstante“ und „Speicher“.

Die Zeitkonstante τ sagt, wie schnell das Haus ohne Heizen zur Außentemperatur hin auskühlt; der
Speicher `C` ist der Strom der Klimaanlage in kWh, der das Haus um 1 K wärmer macht
(`C = τ · u / 1000` mit der Grundleistung `u` in W je K). Im Dry-Run gibt es keine Versatz-Ereignisse;
die Karte zeigt dann „{c} kWh Strom je K“ ohne „(aus {k} Versatz-Ereignissen, …)“.

Prüfschritte:

1. **τ gegen eine beobachtete Nacht:** Suche in Home Assistant (oder im Panel „Klimaanlage“) eine
   Zeit, in der die Klimaanlage einige Stunden nicht lief. Mit der Temperaturdifferenz innen–außen
   `ΔT` zu Beginn und der Dauer `t` erwartet das Modell einen Abfall um etwa `ΔT · (1 − e^(−t/τ))`.
   Beispiel (erfundene Werte): `ΔT` = 15 K, `t` = 6 h, τ = 40 h ergibt etwa 15 · (1 − e^(−0,15)) ≈
   2,1 K. Der beobachtete Abfall von „Klima Ist“ liegt in derselben Größenordnung (Faktor 2 ist noch
   plausibel; in den Bädern kühlt es wegen des Ölofens langsamer).
2. **`C` gegen die Rechnung:** Der Wert der Karte stimmt mit τ mal Grundleistung durch 1000 überein
   (Rundung auf eine Stelle). Beispiel (erfunden): `u` = 120 W je K, τ = 40 h ergibt 4,8 kWh je K.
3. **Größenordnung:** `C` passt zu dem, was du von der Anlage kennst: Nach dem Einschalten an einem
   kalten Morgen braucht die Klimaanlage für 1 K etwa `C` kWh Strom über der Grundleistung. Ein Wert,
   der um ein Vielfaches daneben liegt, ist ein Befund; notiere dann τ, `u` und `R²`.

| Größe | Wert der Karte | eigene Rechnung oder Beobachtung | plausibel |
|---|---|---|---|
| τ (h) | | | ☐ |
| Grundleistung `u` (W je K) | | – | – |
| `C` (kWh Strom je K) | | | ☐ |

Datum: ________ Ergebnis: ☐ bestanden ☐ nicht bestanden Unterschrift: ________

---

## Teil B – live mit Schalter an (14 Tage)

**Voraussetzungen:** Teil A ist bestanden, die Abnahme 6a ist bestanden und der Aktor
`thermal.<name>` ist live (Betriebshandbuch, „Klimaanlage (Phase 6a)“, „Live schalten“), und du hast
dich für Teil B entschieden. Dann schaltest du **Einstellungen** → „Vorheizen und Vorkühlen“ ein (oder
in Home Assistant den Schalter `thermal_plan`); die app plant gleich neu (Anlass „Einstellungen
geändert“). Ab dann steht im Zeitplan „Vorheizen/Vorkühlen aktiv: spart etwa {x} ct.“, wenn es sich
lohnt, und die Karte „Klimaanlage“ auf **System** zeigt in der Zeile „Plan“ den Planwert („{k} K bis
{HH:MM}“ oder „kein Planwert“). Zurück: den Schalter ausschalten; die app sendet den Planwert sofort
nicht mehr, und der core folgt wieder den Regeln aus 6a.

Trage das wirksame Band oben ein. Das „Ist“ der Klimaanlage (UVR A9) ist beim Heizen die niedrigste
Raumtemperatur; das Band gilt also für den ungünstigsten Raum, die anderen Räume liegen beim Vorheizen
ähnlich darüber (Abschnitt B5).

Die Entscheidungen des Zeitraums holst du einmal (`SINCE` ist der Beginn von Teil B in UTC):

```bash
curl -s -H "Authorization: Bearer $BINDAEMS_INTERNAL_TOKEN" \
  "http://127.0.0.1:8081/v1/decisions?since=$SINCE&limit=5000" > klima.json
```

`more` in der Antwort sagt, ob weitere Entscheidungen folgen (Betriebshandbuch, Abschnitt 13,
„Entscheidungslog“); dann holst du den Rest mit einem späteren `since`.

### B1. Ist minus Soll ohne Versatz in mindestens 95 % der Viertelstunden im Komfortband ±0,5 K

**Fundort:** InfluxDB, Messung `thermal` (Grafana, Panel „Klimaanlage“).

„Im Komfortband ±0,5 K“ heißt: im wirksamen Band (oben eingetragen) mit 0,5 K Spiel nach beiden
Seiten. Beim **Heizen** liegt Ist minus Soll ohne Versatz zwischen `−(heat_down_k + 0,5)` und
`+(heat_up_k + 0,5)` K, mit den Standardwerten also zwischen −1,5 und +2,0 K. (Beim Kühlen wäre es
`−(cool_up_k + 0,5)` bis `+(cool_down_k + 0,5)`; Teil B fällt in den Winter.) Soll ohne Versatz ist
Soll inklusive Versatz (`setpoint_c`) minus das Echo (`offset_k`).

Prüfschritte:

1. Die Abweichung je Viertelstunde abfragen und zählen (Zeitraum von Teil B, Grenzen nach deinem
   Band):

   ```bash
   influx -host "$INFLUX_HOST" -username "$INFLUX_USER" -password "$INFLUX_PASSWORD" \
     -database bindaems -precision rfc3339 -format csv -execute "SELECT MEAN(actual_c) - MEAN(setpoint_c) + MEAN(offset_k) AS abweichung FROM raw.thermal WHERE id = 'klima' AND time >= '2026-12-15T00:00:00Z' AND time < '2026-12-29T00:00:00Z' GROUP BY time(15m) fill(none)" \
     > abweichung.csv
   awk -F, -v lo=-1.5 -v hi=2.0 'NR > 1 && $3 != "" { n++; if ($3 >= lo && $3 <= hi) ok++; else print $2, $3 }
     END { printf "%d von %d Viertelstunden im Band (%.1f %%)\n", ok, n, 100 * ok / n }' abweichung.csv
   ```

   Die letzte Zeile nennt den Anteil, davor stehen die Viertelstunden außerhalb.
2. Viertelstunden außerhalb prüfst du einzeln: Lief die Klimaanlage nicht im Heizen (Verlauf von
   „Klima Betriebsart“ in Home Assistant, Lüften oder Aus), zählen sie nicht; zähle sie ab und rechne
   den Anteil ohne sie neu. Nach dem Einschalten am Morgen oder nach einem Ausfall darf das Haus eine
   Weile unter dem Band liegen; auch das notierst du.
3. Bestanden, wenn mindestens **95 %** der Viertelstunden mit Heizen im Band liegen.

| Messung | Wert |
|---|---|
| Viertelstunden mit Werten (Zahl) | |
| davon nicht im Heizen (Zahl) | |
| davon im Band (Zahl) / Anteil | ____ / ____ % ☐ mindestens 95 % |
| größte Abweichung nach oben / unten (K) | |

Datum: ________ Ergebnis: ☐ bestanden ☐ nicht bestanden Unterschrift: ________

### B2. Der Versatz bleibt in ±3 K und folgt der Schrittregel (wie 6a)

**Fundort:** Entscheidungslog (`klima.json`), InfluxDB.

Prüfschritte:

1. Die Abfragen der Abnahme 6a, Abschnitte 4.1 (Bereich) und 4.2 (Schrittregel), laufen unverändert
   auf `klima.json` und auf InfluxDB für den Zeitraum von Teil B
   (`docs/verification/abnahme-phase-6a.md`). Beide Werte von 4.1 liegen in −3 … +3; jede Zeile von
   4.2 hat die Stufe 0,5 und einen Abstand von mindestens 600 s, ohne „Vorzeichenwechsel ohne 0“.
2. Die Entscheidungen mit dem Grund `thermal_plan` (der Wunsch folgt dem Planwert, auch die 0 beim
   Ende des Planwerts) liegen im wirksamen Band:

   ```bash
   jq -r '.decisions[] | select(.actuator == "thermal.klima" and (.dry_run | not)
       and any(.codes[]; . == "thermal_plan"))
       | [.ts, .requested, .applied, .result, .inputs.thermal_steps, .inputs.planned_steps] | @tsv' klima.json
   ```

   `requested` liegt beim Heizen zwischen `−heat_down_k` und `+heat_up_k` (wirksames Band), und
   `result` ist `ok`. Die Safety-Schicht lehnte nichts ab (kein `rejected`).

| Kriterium | Ergebnis |
|---|---|
| 6a 4.1: kleinster / größter Versatz (K) | von ____ bis ____ ☐ innerhalb ±3 K |
| 6a 4.2: Erhöhungen des Betrags (Zahl) | ____ ☐ alle 0,5 K ☐ alle mit Abstand ≥ 600 s |
| Entscheidungen `thermal_plan` (Zahl) / davon im Band | ____ / ____ ☐ |

Datum: ________ Ergebnis: ☐ bestanden ☐ nicht bestanden Unterschrift: ________

### B3. Jeder positive Planwert ging binnen 6 min eine Stufe zurück, während der Akku entlud

**Fundort:** Entscheidungslog (`klima.json`), InfluxDB, Messung `power` (`source` `battery`).

Kein Vorheizen aus dem Akku: Der Plan nimmt Netzstrom für Mehrverbrauch nur in Slots, in denen der
Akku nicht entlädt. Entlädt er trotzdem (die Prognose lag daneben), greift im core der
**Akku-Wächter**: Entlädt der Akku bei einem Planwert über 0 fünf Minuten lang ohne Unterbrechung
mindestens 300 W, geht der Wunsch eine Stufe zurück (Grund `thermal_battery`), und so je weitere 5 min;
der Plan steigt dann nicht. Die Akkuleistung `p_w` ist beim Entladen negativ.

Prüfschritte:

1. Die Entscheidungen mit positivem Planwert und die Schritte des Wächters listen:

   ```bash
   jq -r '.decisions[] | select(.actuator == "thermal.klima" and (.dry_run | not)
       and (.inputs.planned_steps // 0) > 0)
       | [.ts, .requested, .inputs.thermal_steps, .inputs.planned_steps, (.codes | join(","))]
       | @tsv' klima.json
   ```

2. Die Minuten, in denen der Akku mindestens 300 W entlud:

   ```sql
   SELECT MEAN("p_w") FROM "raw"."power"
   WHERE "source" = 'battery' AND "phase" = 'total'
     AND time >= '2026-12-15T00:00:00Z' AND time < '2026-12-29T00:00:00Z'
   GROUP BY time(1m) fill(none)
   ```

   Ein Fall ist eine Folge von mindestens fünf Minuten mit `p_w` von −300 W oder weniger, in der der
   Wunsch (Schritt 1, `thermal_steps`) über 0 lag und der Planwert (`planned_steps`) über 0 war.
3. Zu jedem Fall gibt es binnen 360 s nach dem Beginn der Entladung eine Entscheidung mit
   `thermal_battery` und einem um eine Stufe (0,5 K) kleineren Betrag; hielt die Entladung an, folgt
   je weitere 5 min eine Stufe, und der Wunsch stieg in dieser Zeit nicht. Die Minutenmittel sind
   gröber als der Wächter (er zählt jede Unterbrechung); ein Fall mit einer Minute knapp über −300 W
   zählt nicht.
4. Gab es im Zeitraum keinen Fall, trägst du „kein Fall“ ein; das ist bestanden (der Plan hat dann
   nie gegen den Akku vorgeheizt). Das Verhalten selbst zeigt der Simulator im Szenario
   `klima-plan-akku`.

| Fall (Beginn der Entladung, UTC) | Wunsch / Planwert (Stufen) | Entscheidung `thermal_battery` um | Abstand (s) | ≤ 360 s |
|---|---|---|---|---|
| | | | | ☐ |
| | | | | ☐ |

Datum: ________ Ergebnis: ☐ bestanden ☐ nicht bestanden ☐ kein Fall Unterschrift: ________

### B4. Die geschätzte Ersparnis ist nicht negativ

**Fundort:** Zeitplan → Karte „Vorheizen und Vorkühlen“; Tabelle `plan` der app.

Jeder Plan schätzt die Ersparnis der Verschiebung über seinen ganzen Horizont gegen eine Grundlinie
(der schlichte Weg zurück zum Soll ohne Verschiebung), zu echten Preisen ohne die Marge. Eine
gemessene Ersparnis gibt es in 5c nicht; die Pläne überlappen, ihre Ersparnisse lassen sich nicht
zusammenzählen.

Prüfschritte:

1. Die Pläne von Teil B zählen, wie in A3, mit `SINCE` gleich dem Beginn von Teil B (der Befehl aus A3,
   Schritt 2).
2. Bestanden, wenn keine Ersparnis negativ ist, jede Zeile mit `active` `1` (wahr) den Grund `ok` und
   eine kleinste Ersparnis von mindestens 2,0 ct hat und es keinen Plan mit `active` wahr und einem
   anderen Grund gibt.
3. Stichprobe an drei Tagen: Die Zeile im Zeitplan („Vorheizen/Vorkühlen aktiv: spart etwa {x} ct.“)
   passt zur Spalte „Klima“: vorgeheizt wird in günstigen Stunden oder mit PV-Überschuss, abgesenkt in
   teuren.

| Grund | aktiv | Pläne (Zahl) | Ersparnis kleinste / größte (ct) |
|---|---|---|---|
| `ok` | wahr | | |
| andere | falsch | | |

Datum: ________ Ergebnis: ☐ bestanden ☐ nicht bestanden Unterschrift: ________

### B5. Räume beim Vorheizen höchstens 0,5 K über dem eingestellten Band (Sichtprüfung)

**Fundort:** Home Assistant, Verlauf der Raumtemperaturen; Zeitplan, Spalte „Klima“.

Das Modell regelt auf die niedrigste Raumtemperatur (UVR A9); die einzelnen Räume nutzt 5c nicht. Hier
schaust du selbst nach, ohne Code: In mindestens drei Zeiträumen mit Vorheizen (Spalte „Klima“ mit
positivem Versatz, Zeile „Plan“ der Karte „Klimaanlage“) liegt kein Raum mehr als 0,5 K über dem
eingestellten Band, also über Soll ohne Versatz plus `heat_up_k` plus 0,5 K.

| Zeitraum mit Vorheizen | Soll ohne Versatz + `heat_up_k` (°C) | wärmster Raum (Name, °C) | höchstens 0,5 K darüber |
|---|---|---|---|
| | | | ☐ |
| | | | ☐ |
| | | | ☐ |

Datum: ________ Ergebnis: ☐ bestanden ☐ nicht bestanden Unterschrift: ________

### B6. Du hast nichts Unangenehmes gemerkt

Kein Raum war spürbar zu warm oder zu kalt, kein Luftzug oder Geräusch der Klimaanlage zu ungewohnter
Zeit, das dich gestört hat. Notiere, was dir auffiel, auch wenn es dich nicht störte.

Notizen:

Datum: ________ Ergebnis: ☐ bestanden ☐ nicht bestanden Unterschrift: ________

### Weiteres (kein Kriterium, aber notieren)

- Pläne mit dem Grund `solver` im Zeitraum (Befehl aus A3, Schritt 2): Die Verschiebung ließ sich
  nicht berechnen; der Plan galt ohne sie. Einzelne sind kein Befund, viele schon.
- Kein Alarm `control.thermal_exception` und kein `thermal.echo_mismatch.<name>` im Zeitraum.
- Endet ein Plan (abgelaufen, keine gültige Absicht, andere Betriebsart), stehen die geplanten Stufen
  im selben Zyklus auf 0 (`thermal_plan` mit Versatz 0 in `klima.json`).

---

## Gesamtergebnis

| Abschnitt | Ergebnis |
|---|---|
| A1. Start: Migration, Komponente und Anzeige | ☐ bestanden |
| A2. Das Heizmodell taugt | ☐ bestanden |
| A3. Die Vorschau steht in jedem Plan, mit Ersparnis und ohne Fehler | ☐ bestanden |
| A4. τ und `C` sind plausibel | ☐ bestanden |
| B1. Ist minus Soll ohne Versatz in mindestens 95 % der Viertelstunden im Komfortband ±0,5 K | ☐ bestanden |
| B2. Versatz in ±3 K, Schrittregel wie 6a | ☐ bestanden |
| B3. Akku-Wächter binnen 6 min | ☐ bestanden ☐ kein Fall |
| B4. Geschätzte Ersparnis nicht negativ | ☐ bestanden |
| B5. Räume höchstens 0,5 K über dem Band (Sichtprüfung) | ☐ bestanden |
| B6. Nichts Unangenehmes gemerkt | ☐ bestanden |

Teil A ist abgenommen, wenn A1 bis A4 bestanden sind; danach entscheidest du, ob Teil B folgt. Die
Phase 5c ist abgenommen, wenn auch B1 bis B6 bestanden sind (B3 auch ohne Fall). Bis dahin bleibt
der Schalter aus, oder du schaltest ihn nach Teil B wieder aus, wenn dir etwas nicht gefiel. Das
Kühlmodell gibt es frühestens im Sommer 2027; seine Abnahme folgt dann mit denselben Schritten.

Datum: ________ Unterschrift: ________
