# Abnahme Phase 5b – Checkliste (Fahrverhalten)

Spec Abschnitt 18, Phase 5, „Abnahme“, Teil Fahrverhalten: Vorschläge erscheinen mit Konfidenz. Dazu
kommt, was Plan 5b zusagt (Entscheidung 16): **28 Tage ab dem Ausrollen**, und in dieser Zeit

1. für jedes Auto mit Daten erscheinen Vorschläge mit Konfidenz auf „Prognosen“, „Laden“ und in
   Home Assistant,
2. für jedes Auto mit mindestens 10 bewerteten Tagen sind die Trefferquote der Abfahrt und die des
   Ziels je mindestens 80 %,
3. mit eingeschalteter Automatik (wenn du sie einschaltest) entsteht keine Zeile „geändert“ durch die
   Automatik in „Geplante Ladungen“ und keine Änderung während einer laufenden geplanten Ladung,
4. ein erwartetes Auto steht im Zeitplan und wird beim Anstecken durch das echte ersetzt (einmal an der
   Anlage beobachtet).

Der Teil Lastmodell der Phase 5 steht in `abnahme-phase-5a.md`.

Diese Checkliste füllst du an der Anlage aus. Kopiere sie dafür nach
`docs/verification/JJJJ-MM-TT-abnahme-phase-5b.md`, trage die Ergebnisse ein und checke sie ein.
Voraussetzung: Plan 5b ist ausgerollt, gemeinsam mit 3d, 4b, 5a und 6a und damit nach der Abnahme der
Phase 3 (Betriebshandbuch, Abschnitt „Fahrverhalten (Phase 5b)“, „Fahrverhalten ausrollen (Plan 5b)“).
Die 28 Tage beginnen mit dem Ausrollen. **Nichts geht von selbst live:** Die Automatik ist ab
Werk aus, erwartete Autos ändern nur den Plan, und gelernte Werte erreichen die Safety-Schicht nie
(Spec 10.5).

Alle Abfragen laufen mit Anmeldung (`jar` und `$APP` wie im Betriebshandbuch, Abschnitt 8,
„Alternative für Skripte (curl)“). Zeiten in der API und in der Datenbank sind UTC; die Seite zeigt
Ortszeit.

| Angabe | Wert |
|---|---|
| Anlage | |
| Version core / app (UI → System) | |
| Ausgerollt am (Beginn der 28 Tage) | |
| Ende der 28 Tage | |
| Autos mit Anwesenheit aus Home Assistant (Schlüssel, Entität) | |
| Automatik eingeschaltet für (Schlüssel, ab wann) | |
| Prüfer | |

---

## 1. Start: erste Berechnung und Komponente

**Fundort im UI:** System → „Komponenten“, `driving`; Prognosen → Karte „Fahrverhalten“.

Prüfschritte:

1. Nach dem Start läuft die Migration `0014`, und die app berechnet das Fahrverhalten gleich
   (Nachtlauf sonst um 03:40 Uhr). Die Komponente `driving` meldet „Fahrverhalten aktuell“ oder, bis es
   Tage gibt, „Noch keine Fahrdaten“; „Fahrverhalten fehlgeschlagen: {Fehler}“ ist ein Befund:

   ```bash
   cd /opt/bindaems
   docker compose logs ems-app | grep -i "Fahrverhalten"
   ```

2. Die Karte „Fahrverhalten“ nennt je Auto den Kopf „{Name} · Quelle: {Quelle} · {n} Tage“. Trage die
   Quelle ein: „Home Assistant ({entity})“, „Home Assistant (home_entity)“, „Tessie“ oder „nur
   Ansteckvorgänge“. Beim Tesla sollte es der `device_tracker.…` aus Home Assistant sein.

| Auto | Quelle | Tage | erste Berechnung am |
|---|---|---|---|
| | | | |
| | | | |

Datum: ________ Ergebnis: ☐ bestanden ☐ nicht bestanden Unterschrift: ________

---

## 2. Vorschläge mit Konfidenz (Kriterium 1)

**Fundort im UI:** Prognosen → Karte „Fahrverhalten“; Laden → Fahrzeugkarte; Home Assistant → Gerät
„BindaEMS <Fahrzeug>“.

Für **jedes Auto mit Daten** erscheinen Vorschläge mit Konfidenz an drei Stellen:

- **Prognosen:** je Wochentag „Abfahrt“, „Vorschlag Ziel“ und „Konfidenz“ („hoch“, „mittel“,
  „niedrig“); ein Wochentag ohne Fahrt zeigt „keine Fahrt erwartet“.
- **Laden:** auf der Fahrzeugkarte „Vorschlag: {Tag} {HH:MM} · {x} % (Konfidenz {k})“ mit der Taste
  „Übernehmen“. Ohne `next_trip` steht dort „Kein Vorschlag (zu wenig Daten).“. Das gilt auch für ein
  Auto mit Daten, das in den nächsten acht Tagen keine Fahrt erwartet (etwa weil der Fahrtanteil seiner
  Wochentage unter 50 % liegt). Für dieses Auto ist Kriterium 1 **nicht** weicher: Es braucht dann die
  Karte „Fahrverhalten“ auf „Prognosen“ mit der Konfidenz je Wochentag (Zeilen mit Fahrt) und die
  Sensoren in Home Assistant, soweit sie einen Vorschlag haben; trage in der Tabelle ein, wo es fehlt,
  und warum.
- **Home Assistant:** die Sensoren „Vorschlag Abfahrt“ und „Vorschlag Ziel-SOC“ mit den Attributen
  `confidence` („hoch“, „mittel“, „niedrig“) und `basis`.

Die Daten stehen auch in der API (`GET /api/learning/driving`):

```bash
curl -s -b jar "$APP/api/learning/driving" | jq '.vehicles[] | {key, source, days, next_trip}'
```

`next_trip` enthält `departure`, `target_soc_pct` und die beiden Konfidenzen. Ein Auto ohne Stand hat
`days` gleich 0 und kein `next_trip`; es hat keine Daten und gehört nicht zum Kriterium.

| Auto | Tage | Prognosen (Konfidenz Abfahrt / Ziel) | Laden (Text) | HA (`confidence`) |
|---|---|---|---|---|
| | | | | |
| | | | | |

Notizen (Autos ohne Vorschlag und Grund, etwa „zu wenig Tage“):

Datum: ________ Ergebnis: ☐ bestanden ☐ nicht bestanden Unterschrift: ________

---

## 3. Trefferquote (Kriterium 2)

**Fundort im UI:** Prognosen → Karte „Fahrverhalten“, Fußzeile „Trefferquote 28 Tage: Abfahrt {x} %
({n} Tage), Ziel {y} % ({m} Tage)“.

Die Trefferquote vergleicht für jeden Fahrtag der letzten 28 Tage die tatsächliche erste Abfahrt mit
dem Vorschlag, der am Vorabend galt (Konfidenz mindestens „mittel“): **Abfahrt getroffen**, wenn das
Auto nicht früher fuhr; **Ziel getroffen**, wenn der vorgeschlagene Ziel-SOC minus dem tatsächlichen
Verbrauch mindestens der Puffer bei der Rückkehr ist. Steht dort „Trefferquote: noch zu wenig Tage“,
gibt es noch keine Zahl.

Prüfschritte:

1. Am Ende der 28 Tage die Zahlen je Auto abrufen (`GET /api/learning/driving`, `quality`):

   ```bash
   curl -s -b jar "$APP/api/learning/driving" | jq '.vehicles[] | {key, quality}'
   ```

2. Für jedes Auto mit `departure_n` und `target_n` je mindestens 10 (bewertete Tage) müssen
   `departure_hit_pct` und `target_hit_pct` je mindestens **80** sein. Ein Auto mit weniger als 10
   bewerteten Tagen zählt nicht zum Kriterium; trage es mit seiner Zahl der Tage ein.
3. Liegt eine Quote darunter, schau dir die verfehlten Tage an. Die Karte „Fahrverhalten“ zeigt keine
   einzelnen Abfahrten, und die Trefferquote liegt nur im Speicher der app. Die tatsächlichen Abfahrten
   liest du aus zwei Quellen: Das Ende der Ansteckvorgänge steht in der Tabelle `charging_session` der
   app (nur lesend; die Zeiten sind UTC, Ortszeit ist eine bzw. zwei Stunden mehr):

   ```bash
   cd /opt/bindaems
   docker compose exec ems-app python -c "import sqlite3; db = sqlite3.connect('file:/data/bindaems.sqlite3?mode=ro', uri=True); [print(r) for r in db.execute(\"select vehicle, start, end from charging_session where vehicle = 'tesla' order by end desc limit 60\")]"
   ```

   (`'tesla'` durch den Schlüssel des Autos ersetzen.) Das ist die Abfahrt, wenn das Auto beim Losfahren
   abgesteckt wurde. Die genaue Abfahrt (Wechsel „zu Hause“ → „weg“) steht im Verlauf der Anwesenheit
   des Autos in Home Assistant (Verlauf der Entität `device_tracker.…`, bzw. `home_entity`). Vergleiche
   sie mit „Abfahrt“ (P20) und „Konfidenz“ des Wochentags auf „Prognosen“ und notiere die Ursache (etwa
   ein geänderter Arbeitsweg, Urlaub, ein Auto, das nur selten fährt). Das Kriterium ist dann nicht
   erfüllt; die Ursache entscheidet, ob du die Parameter (Puffer, Bereich) änderst und die 28 Tage neu
   beginnst.

| Auto | `departure_n` | `departure_hit_pct` | `target_n` | `target_hit_pct` | ≥ 80 % |
|---|---|---|---|---|---|
| | | | | | ☐ |
| | | | | | ☐ |

Datum: ________ Ergebnis: ☐ bestanden ☐ nicht bestanden ☐ nicht bewertbar (unter 10 Tage)
Unterschrift: ________

---

## 4. Automatik: keine Änderung durch die Automatik an geplanten Ladungen (Kriterium 3)

**Fundort im UI:** Laden → Fahrzeugkarte (Schalter „Ziel und Abfahrt automatisch“ und der Satz zum
Zustand); Statistik → „Monat“ → Karte „Geplantes Laden“; Protokoll (nur Admin).

Dieser Abschnitt gilt nur, wenn du die Automatik für ein Auto einschaltest. Schaltest du sie nicht
ein, trägst du „Automatik nicht eingeschaltet“ ein; die Abnahme ist dann ohne diesen Abschnitt
möglich, die Automatik bleibt aber unabgenommen.

Die Automatik setzt Ziel und Abfahrt nur, wenn **keine geplante Ladung des Autos läuft** (Zustand
`waiting`: „Automatik wartet: Eine geplante Ladung läuft bis {Tag} {HH:MM}.“). Nur in den ersten 10 min
einer Zeile übernimmt die Zeile die Werte der Automatik, statt als „geändert“ zu enden. **Das ist
erlaubt** (Entscheidung 8 des Plans): Eine Änderung der Automatik in den ersten 10 min einer Zeile zählt
weder als „geändert“ noch als Änderung während der Ladung, die Zeile übernimmt die Werte. Eine Änderung
von Hand pausiert die Automatik bis zur gesetzten Abfahrt (`paused`).

Prüfschritte:

1. Die Änderungen der Automatik stehen im Änderungsprotokoll mit dem Urheber „Automatik“ (Admin:
   Seite „Protokoll“ oder `GET /api/audit`). Das Ein- und Ausschalten steht dort als
   `control.automation` („Automatik an“, „Automatik aus“) mit dem Benutzer. Trage die Zeiten ein, zu
   denen die Automatik Ziel und Abfahrt gesetzt hat:

   ```bash
   cd /opt/bindaems
   docker compose exec ems-app python -c "import sqlite3; db = sqlite3.connect('file:/data/bindaems.sqlite3?mode=ro', uri=True); [print(r) for r in db.execute(\"select ts, action, target, details from audit_log where actor = 'Automatik' order by ts\")]"
   ```

2. Die geplanten Ladungen mit dem Ergebnis `changed` (die Zeile „Vor der Abfahrt geändert oder
   verlassen: {n}“ der Karte „Geplantes Laden“ zählt sie) lesen, nur lesend:

   ```bash
   docker compose exec ems-app python -c "import sqlite3; db = sqlite3.connect('file:/data/bindaems.sqlite3?mode=ro', uri=True); [print(r) for r in db.execute(\"select vehicle, started_at, departure_at, target_soc_pct, outcome from planned_charge where outcome = 'changed' order by started_at\")]"
   ```

3. Für jede `changed`-Zeile ab dem Einschalten der Automatik stellst du fest, ob zwischen `started_at`
   und der Abfahrt ein Eintrag der Automatik für dasselbe Auto steht (Schritt 1). **Keiner darf es
   sein:** Eine Zeile „geändert“ ist nur durch deine Änderung von Hand erlaubt (dann steht sie ohne
   Eintrag der Automatik und mit einem Eintrag von dir im Protokoll).
4. Kein Eintrag der Automatik liegt während einer laufenden geplanten Ladung des Autos: Zwischen
   `started_at` und der Abfahrt (offene Zeile) hat die Automatik **außer in den ersten 10 min der Zeile
   (erlaubt, siehe oben)** nichts geändert. Die Zustände `waiting` und `paused` während der Ladung auf der Fahrzeugkarte
   bestätigen es.

| Auto | Automatik an seit | Einträge „Automatik“ (Zahl) | Zeilen `changed` seit dem Einschalten | davon durch die Automatik | Änderungen während einer laufenden Ladung |
|---|---|---|---|---|---|
| | | | | 0 ☐ | 0 ☐ |
| | | | | 0 ☐ | 0 ☐ |

Notizen (jede `changed`-Zeile mit Ursache, etwa „von Hand am …“):

Datum: ________ Ergebnis: ☐ bestanden ☐ nicht bestanden ☐ Automatik nicht eingeschaltet
Unterschrift: ________

---

## 5. Erwartetes Auto im Zeitplan, beim Anstecken ersetzt (Kriterium 4)

**Fundort im UI:** Zeitplan; Laden.

Ein Auto in „Geplant“, das nicht angesteckt ist, kommt als **erwartetes Auto** in den Plan, wenn die
Anwesenheitsprognose für eine spätere Viertelstunde vor seiner Abfahrt mindestens 50 % sagt, es einen
gelernten Ladestand bei der Ankunft und eine `default_wallbox` hat. Der core bekommt dafür nichts.
Beobachte das **einmal** an der Anlage, am besten an einem Abend, an dem der Tesla zurückkommt:

Prüfschritte:

1. Das Auto steht auf „Geplant“ und ist nicht angesteckt (Health: `plugged` falsch). Vor der
   erwarteten Ankunft steht auf **Zeitplan** der Eintrag „{Name} erwartet ab {HH:MM}“ (gestrichelt) und
   im Ablauf der Satz „{name} wird um {um} Uhr mit etwa {soc} % erwartet (Anwesenheitsprognose).“
   (Grundcode `AUTO_ERWARTET`). In der API:

   ```bash
   curl -s -b jar "$APP/api/plan" | jq '.plan.vehicles[] | {key, expected, arrival_at}'
   ```

   `expected` ist wahr, `arrival_at` die erwartete Ankunft. Trage beides ein.
2. Das Auto kommt an und wird **angesteckt**. Der Auslöser `vehicles` („Fahrzeug geändert“) plant neu;
   der neue Plan nennt das Auto mit `expected` gleich falsch, mit dem echten Ladestand, und der Eintrag
   „erwartet ab“ ist weg. Trage Zeit des Ansteckens und des neuen Plans (Seite „Zeitplan“, „Plan von
   HH:MM Uhr“, „Anlass“) ein.
3. Plausibel: In der Zeit vor der erwarteten Ankunft lädt der Plan das Auto nicht, und der core hat nie
   Leistung für das erwartete Auto bekommen (Health nennt für das nicht angesteckte Auto kein
   `planned`).

| Schritt | Beobachtet |
|---|---|
| Auto, Datum | |
| Eintrag „erwartet ab {HH:MM}“ (Zeitplan) | ☐ |
| `expected` / `arrival_at` im Plan | |
| Angesteckt um | |
| Neuer Plan (Zeit, Anlass „Fahrzeug geändert“), `expected` falsch | ☐ |
| Eintrag „erwartet ab“ danach weg | ☐ |

Datum: ________ Ergebnis: ☐ bestanden ☐ nicht bestanden Unterschrift: ________

---

## Gesamtergebnis

| Abschnitt | Ergebnis |
|---|---|
| 1. Start: erste Berechnung und Komponente | ☐ bestanden |
| 2. Vorschläge mit Konfidenz (Prognosen, Laden, HA) | ☐ bestanden |
| 3. Trefferquote der Abfahrt und des Ziels je mindestens 80 % | ☐ bestanden |
| 4. Automatik: keine Zeile „geändert“, keine Änderung während der Ladung | ☐ bestanden ☐ nicht eingeschaltet |
| 5. Erwartetes Auto im Zeitplan, beim Anstecken ersetzt | ☐ bestanden |

Der Teil Fahrverhalten der Phase 5 ist abgenommen, wenn die Abschnitte 1, 2, 3 und 5 bestanden sind
und Abschnitt 4 bestanden ist oder die Automatik nicht eingeschaltet wurde. Zusammen mit der Abnahme
5a ist dann die Phase 5 abgenommen (5c, Vorheizen und Vorkühlen, folgt später).

Datum: ________ Unterschrift: ________
