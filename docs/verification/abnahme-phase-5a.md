# Abnahme Phase 5a – Checkliste (Lastmodell)

Spec Abschnitt 18, Phase 5, „Abnahme“, Teil Lastmodell: Das Modell ist über 4 Wochen um mindestens
10 % besser (mittlerer absoluter Fehler, MAE) als das Profil, **oder** das Profil bleibt automatisch
aktiv. Dazu kommt, was Plan 5a zusagt (Entscheidung 16): jede Nacht ein Training, keine Fehler in der
Komponente `load-model` und der Rückfall bei fehlender Modelldatei, an der Anlage beobachtet. Der
zweite Teil der Abnahme der Phase 5, „Vorschläge erscheinen mit Konfidenz“, gehört zum Fahrverhalten
(Plan 5b) und steht hier nicht.

Diese Checkliste füllst du an der Anlage aus. Kopiere sie dafür nach
`docs/verification/JJJJ-MM-TT-abnahme-phase-5a.md`, trage die Ergebnisse ein und checke sie ein.
Voraussetzung: Plan 5a ist ausgerollt (nach (oder mit) 4b und 6a und damit nach der Abnahme der
Phase 3; Betriebshandbuch, Abschnitt „Lastmodell (Phase 5a)“, „Lastmodell ausrollen (Plan 5a)“). Die
28 Tage beginnen mit dem Ausrollen. Plan 5a schaltet nichts an der Anlage: Das Modell ändert nur die
Eingabe des Planers.

Alle Abfragen laufen mit Anmeldung (`jar` und `$APP` wie im Betriebshandbuch, Abschnitt 8,
„Alternative für Skripte (curl)“). Zeiten in der API und in der Datenbank sind UTC; die Seite zeigt
Ortszeit.

| Angabe | Wert |
|---|---|
| Anlage | |
| Version core / app (UI → System) | |
| Ausgerollt am (Beginn der 28 Tage) | |
| Ende der 28 Tage | |
| Gewählte Sensoren (Einstellungen → Lastmodell) | |
| Prüfer | |

---

## 1. Start: erstes Training

**Fundort im UI:** Prognosen → Karte „Lastprognose“; System → „Komponenten“, `load-model`.

Prüfschritte:

1. Beim Start der app läuft die Migration `0013`, die Temperatur-Historie wird einmal nachgeholt
   (im Log „Temperatur-Historie nachgeholt“) und das erste Training beginnt gleich danach:

   ```bash
   cd /opt/bindaems
   docker compose logs ems-app | grep -i "Temperatur-Historie\|Lastmodell trainiert\|Zu wenig Daten für das Lastmodell"
   ```

2. Hat die Abrechnung 28 Tage mit je mindestens 80 von 96 Viertelstunden, steht in der Karte
   „Lastprognose“ „Trainiert am {TT.MM. HH:MM} mit {n} Viertelstunden.“ und „Modell vom
   {TT.MM.JJJJ HH:MM}“. Sonst steht dort „Zu wenig Daten: {n} von 28 Tagen.“; dann beginnt die
   Abnahme mit dem Tag, an dem das erste Training gelingt, und du trägst ihn hier ein.
3. Gleich nach dem ersten Training ist die Aktivierung bewertet: Die Karte „Lastprognose“ nennt
   „Verfahren: Modell“ oder „Verfahren: Profil“ und den Grund. Mit „Lastmodell verwenden“ an (ab
   Werk) kann das Modell also schon jetzt aktiv sein, mit dem Backtest als Grundlage („… (im
   Backtest).“); der Planer rechnet dann mit seiner Lastprognose. Der Backtest ist in den ersten Wochen
   zu günstig (Training und Backtest rechnen mit der archivierten Temperatur). Willst du zuerst einen
   reinen Schattenbetrieb, schalte „Lastmodell verwenden“ jetzt aus (Einstellungen → Lastmodell) und
   später an derselben Stelle wieder ein; die app bewertet dann sofort neu. Profil und Modell rechnen
   in dieser Zeit beide weiter, die Prognosegüte (Abschnitt 3) bleibt also vergleichbar; in
   Abschnitt 2 steht in diesen Wochen „Profil aktiv: Das Lastmodell ist in den Einstellungen
   ausgeschaltet.“
4. Optional: Sensoren wählen (Einstellungen → Lastmodell), dann „Jetzt neu trainieren“.

| Angabe | Wert |
|---|---|
| Temperatur-Historie nachgeholt (Zahl der Slots im Log) | |
| Erstes Training am / „Trainiert am …“ | |
| Verfahren und Grund gleich nach dem ersten Training | |
| Reiner Schattenbetrieb mit „Lastmodell verwenden“ aus (von–bis, oder „nein“) | |
| Beginn der 28 Tage (erstes brauchbares Modell) | |

Datum: ________ Ergebnis: ☐ bestanden ☐ nicht bestanden Unterschrift: ________

---

## 2. Verfahren und Grund in der Karte „Lastprognose“

**Fundort im UI:** Prognosen → Karte „Lastprognose“.

Die Karte nennt „Verfahren: Profil“ oder „Verfahren: Modell“ und darunter den Grund der Aktivierung.
Die Gründe sind (Betriebshandbuch, „Aktivierung“):

- „Modell aktiv: in den letzten 14 Tagen {x} % genauer als das Profil ({Grundlage}).“ mit der Grundlage
  „im Schattenbetrieb“ oder „im Backtest“
- „Profil aktiv: Das Modell ist nur {x} % genauer, nötig sind 10 %.“ oder „Profil aktiv: Das Modell ist
  {x} % ungenauer als das Profil.“
- „Profil aktiv: Für den Vergleich fehlen noch Tage ({n} von 10).“
- „Profil aktiv: Das Lastmodell ist in den Einstellungen ausgeschaltet.“
- „Profil aktiv: Es gibt noch kein brauchbares Modell.“

Prüfschritte:

1. Einmal in der Woche (mindestens) die Karte ansehen und eintragen: Verfahren, Satz des Grundes,
   „Modell vom …“. Das Modell ist nur aktiv, wenn der Satz „Modell aktiv“ lautet; steht dort ein
   „Profil aktiv“, gilt das Profil, und die Prognose des Planers (`GET /api/forecast/load`,
   `method`) heißt `profile`.
2. Die Aktivierung wird jede Nacht nach dem Training bewertet; das Log hält jede Entscheidung fest
   (Docker rotiert die Logs, sichere sie darum am Ende jeder Woche):

   ```bash
   docker compose logs --since 168h ems-app | grep "Aktivierung des Lastmodells bewertet"
   ```

   Jede Zeile nennt `reason`, `active` und `basis`. `active` ist genau bei `reason` gleich `better`
   wahr.

| Woche | Datum | Verfahren | Satz des Grundes | „Modell vom …“ | passt zum Log |
|---|---|---|---|---|---|
| 1 | | | | | ☐ |
| 2 | | | | | ☐ |
| 3 | | | | | ☐ |
| 4 | | | | | ☐ |

Datum: ________ Ergebnis: ☐ bestanden ☐ nicht bestanden Unterschrift: ________

---

## 3. Prognosegüte über 28 Tage

**Fundort im UI:** Prognosen → Karte „Prognosegüte“ mit dem Zeitraum „28 Tage“, dazu „Fehler je Tag“ und
„Backtest“.

Prüfschritte:

1. Zeitraum „28 Tage“ wählen. Die Zeilen „Last Profil“ und „Last Modell“ stehen je Vorlauf (0–6 h,
   6–24 h, über 24 h) mit „MAE“; „besser als Profil“ steht bei „Last Modell“ und rechnet gegen das
   Profil derselben Vorlaufklasse. Trage die Werte ein. Sie zeigen, wo das Modell gut oder schlecht
   ist; **keine einzelne dieser Zeilen ist das Kriterium.**
2. Das Kriterium ist die Verbesserung **über alle Vorlaufklassen zusammen** (nach Viertelstunden
   gewichtet), so wie die Regel der Aktivierung die Klassen zusammenfasst. Die Zahl für die 28 Tage
   liefert dieser Abruf; trage sie in die Zeile „Gesamt (jq)“ ein:

   ```bash
   curl -s -b jar "$APP/api/forecast/quality?days=28" | jq '
     def mae(k): [.rows[] | select(.kind == k)] | (map(.n * .mae_w) | add) / (map(.n) | add);
     {profile_mae_w: mae("load_profile"), model_mae_w: mae("load_model"),
      improvement_pct: ((1 - mae("load_model") / mae("load_profile")) * 100)}'
   ```

3. Die Karten „Fehler je Tag“ (Linien „Profil“ und „Modell“) und „Backtest“ (Zeile „Gesamt“) ansehen
   und die Auffälligkeiten notieren, etwa Tage, an denen das Modell deutlich schlechter war.

Das Kriterium ist erfüllt, wenn `improvement_pct` aus dem Abruf **mindestens 10** ist. Der Satz der
Aktivierung in der Karte „Lastprognose“ („… {x} % genauer als das Profil …“) fasst die Klassen
genauso zusammen, gilt aber für die letzten 14 Tage; er ist der Nachweis für Abschnitt 2, nicht für
die 28 Tage. Ist es das nicht, gilt die
Alternative aus Abschnitt 2: Das Profil war automatisch aktiv, sobald und solange das Modell die
10 % nicht schaffte; die Abnahme besteht dann mit den Wochen aus Abschnitt 2 als Nachweis (keine
Woche mit „Modell aktiv“, in der das Modell die 10 % nicht erreichte, und kein „Profil aktiv“ bei
einem Modell, das sie erreichte).

| Vorlauf | MAE Last Profil | MAE Last Modell | besser als Profil (Seite) |
|---|---|---|---|
| 0–6 h | | | |
| 6–24 h | | | |
| über 24 h | | | |
| **Gesamt (jq)** | | | |

Notizen zu „Fehler je Tag“ und „Backtest“:

Datum: ________ Ergebnis: ☐ Modell ≥ 10 % besser ☐ Profil blieb automatisch aktiv ☐ nicht bestanden
Unterschrift: ________

---

## 4. Registry: jede Nacht eine Version

**Fundort:** Datenbank der app, Tabelle `model_registry`; `GET /api/learning/load`, `model.trained_at`.

Jedes Training (03:15 Uhr Ortszeit, in der Nacht also etwa 01:15 oder 02:15 UTC) legt eine neue Version
an; die Registry behält die 14 neuesten und die aktive. Zwei Abfragen, nach 14 und nach 28 Tagen,
belegen dann 28 Nächte. Jedes zusätzliche Training (beim Start, über „Jetzt neu trainieren“, die Probe
aus Abschnitt 6) zählt mit und verkürzt das sichtbare Fenster; zweite Quelle ist die Logzeile
„Lastmodell trainiert“, eine je Lauf:

```bash
cd /opt/bindaems
docker compose exec ems-app python -c "
import sqlite3
c = sqlite3.connect('file:/data/bindaems.sqlite3?mode=ro', uri=True)
for row in c.execute('select id, trained_at, samples, usable, active from model_registry order by id desc'):
    print(*row)"
```

Erwartet: je Nacht eine Zeile (aufeinanderfolgende Nummern und Tage, Zeit um 03:15 Ortszeit, `usable`
gleich 1). Eine Nacht ohne Zeile ist ein Befund: Das Log nennt den Grund („Zu wenig Daten für das
Lastmodell“, „Training des Lastmodells fehlgeschlagen“).
Zusätzlich zeigt die Karte „Lastprognose“ („Modell vom …“) die jüngste Version; an einem Morgen ohne
Eintrag der Nacht ist das ein Hinweis.

| Abfrage am | Zeilen (Zahl) | Nächte ohne Version (Datum, Grund) |
|---|---|---|
| Tag 14 | | |
| Tag 28 | | |

Datum: ________ Ergebnis: ☐ bestanden ☐ nicht bestanden Unterschrift: ________

---

## 5. Keine Fehler in `load-model`

**Fundort im UI:** System → „Komponenten“, Zeile `load-model`.

Die Komponente meldet „Lastmodell aktiv“ oder „Profil aktiv, Modell bereit“. Ein Befund sind „Noch
kein Modell“ nach dem ersten erfolgreichen Training (die Seite zeigt es als „in Ordnung“, weil es
vor dem ersten Training normal ist) und „Lastmodell fehlgeschlagen: {Fehler}“ („gestört“).

Prüfschritte:

1. Bei jeder Kontrolle aus Abschnitt 2 auch die Zeile `load-model` ansehen.
2. Am Ende des Zeitraums das Log nach Warnungen und Fehlern des Lastmodells durchsehen (soweit
   Docker es aufbewahrt, Abschnitt 2):

   ```bash
   docker compose logs --since 672h ems-app | grep -i "lastmodell\|Temperatur-Historie" | grep -i "warn\|error\|fehlgeschlagen\|nicht"
   ```

   Jede Zeile ist erklärt (zum Beispiel der Rückfall aus Abschnitt 6) oder ein Befund.

| Befund (Datum, Meldung) | Ursache | erledigt |
|---|---|---|
| | | ☐ |

Datum: ________ Ergebnis: ☐ bestanden ☐ nicht bestanden Unterschrift: ________

---

## 6. Rückfall bei fehlender Modelldatei

**Fundort im UI:** Prognosen → „Lastprognose“; System → `load-model`.

Das Beispiel einer Rücksicherung der Datenbank: Die Registry kennt eine Version, deren Datei fehlt.
Führe die Probe in einer ruhigen Stunde aus, während „Verfahren: Modell“ gilt (sonst sieht man den
Rückfall nicht). Plan 5a schaltet nichts an der Anlage, die Probe ändert also nur, welche Lastprognose
der Planer bekommt.

Prüfschritte:

1. Auf der Seite „Prognosen“ die Nummer der Version notieren (`GET /api/learning/load`,
   `model.id`) und „Verfahren: Modell“ sehen.
2. `ems-app` stoppen und die Datei der neuesten Version umbenennen (`<Nummer>` aus Schritt 1):

   ```bash
   cd /opt/bindaems
   docker compose stop ems-app
   docker compose run --rm --no-deps --entrypoint sh ems-app -c \
     'mv /data/models/load/load-<Nummer>.txt /data/models/load/load-<Nummer>.txt.weg'
   docker compose start ems-app
   ```

3. Das Log nennt die Warnung „Lastmodell nicht lesbar, die Version wird nicht mehr verwendet“. Sofort
   nach dem Start steht auf der Seite „Prognosen“ **„Verfahren: Profil“** mit „Profil aktiv: Es gibt
   noch kein brauchbares Modell.“ und „Noch kein Modell trainiert.“; die Komponente `load-model`
   nennt „Noch kein Modell“. `GET /api/forecast/load` hat `method` gleich `profile`.
4. Die app trainiert ohne brauchbare Version gleich nach dem Start selbst (wartet dazu höchstens
   10 min auf die Nachholung der Temperatur) und zeigt dabei „Training läuft …“. Ist es zu schnell
   vorbei, um den Zustand aus Schritt 3 zu sehen, beobachte das Log. Die Taste „Jetzt neu
   trainieren“ geht auch von Hand, solange kein Training läuft.
5. Nach dem Training steht eine neue Version da („Modell vom …“ mit der aktuellen Zeit,
   `model.id` größer als zuvor), `load-model` meldet „Lastmodell aktiv“ oder „Profil aktiv, Modell
   bereit“, und die Aktivierung ist neu bewertet. Die umbenannte Version bleibt als unbrauchbar
   markiert; die Datei `….weg` darfst du löschen.

| Schritt | Beobachtet |
|---|---|
| Version vor der Probe (`model.id`) | |
| Warnung im Log | ☐ |
| „Verfahren: Profil“ und `load-model` „Noch kein Modell“ | ☐ |
| Neue Version nach dem Training (`model.id`, „Modell vom …“) | |
| `load-model` danach | |

Datum: ________ Ergebnis: ☐ bestanden ☐ nicht bestanden Unterschrift: ________

---

## Gesamtergebnis

| Abschnitt | Ergebnis |
|---|---|
| 1. Start: erstes Training | ☐ bestanden |
| 2. Verfahren und Grund in der Karte „Lastprognose“ | ☐ bestanden |
| 3. Prognosegüte über 28 Tage (≥ 10 % oder Profil automatisch aktiv) | ☐ bestanden |
| 4. Registry: jede Nacht eine Version | ☐ bestanden |
| 5. Keine Fehler in `load-model` | ☐ bestanden |
| 6. Rückfall bei fehlender Modelldatei | ☐ bestanden |

Der Teil Lastmodell der Phase 5 ist abgenommen, wenn alle sechs Abschnitte bestanden sind. Der Teil
Fahrverhalten folgt mit Plan 5b.

Datum: ________ Unterschrift: ________
