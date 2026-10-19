# Plan 6a Klimaanlage – Festlegungen der Umsetzung und offene Kleinigkeiten

Plan: `docs/superpowers/plans/2026-10-19-phase-6a-klimaanlage.md` (13 Tasks und 16 Entscheidungen,
freigegeben vom Betreiber am 19.10.2026: „Passt so bitte umsetzen“). Spec: Abschnitt 18, Phase 6a.
Ausführung subagent-driven auf dem Branch `claude/project-thread-79ieqs`: je Task eine Umsetzung und
ein Review, dazu Task 12b für die Funde aus dem Simulator, dann ein Gesamtreview, eine Fix-Welle und
ein Re-Review („bereit für `main`“, kein kritischer und kein wichtiger Befund offen).

Jede Festlegung nennt, was sie kostet, falls sie falsch ist.

## Festlegungen (R1 bis R32)

| Nr. | Festlegung | Grund | Kosten, falls falsch |
|---|---|---|---|
| R1 | Der Simulator hat einen eigenen Datagramm-Port in `src/bindaems/tools/sim`. | `src` darf nicht aus `tests` importieren. | etwas doppelter Code |
| R2 | Die Schrittregel prüft live gegen das Echo, sonst (Dry-Run) gegen den zuletzt entschiedenen Wert. | Entscheidung 8 | Der Dry-Run-Log zeigt ein anderes Muster an Annahmen. |
| R3 | Der Health-Block `thermal` entsteht in Task 5; Wunsch, Grund und Hinweis füllt Task 8. | Task 5 bleibt allein prüfbar. | keine |
| R4 | Der Wunsch steht nicht in Influx, nur im Entscheidungslog. | Der Sampler liest Signale, nicht den Zustand der Regelung. | eine fehlende Grafana-Linie |
| R5 | `scaled()` mit Einheit `None`: Paket-Einheit 1 oder 7 heißt Rohwert/10, sonst der Faktor `scale`, sonst `None`. | Präzisierung 1: ohne Einheit gilt die des Pakets. | Leistung bleibt `None`, bis die Einheit eingetragen ist. |
| R6 | `set_offset` lehnt \|v\| > `max_offset_k` ab. | zweite Sicherung neben der Safety-Schicht (Entscheidung 3) | keine |
| R7 | Ein Paket des C.M.I. ohne Wert der Empfangstabelle gilt nicht als Lebenszeichen. | Sonst blieben die Werte bei fremdem Verkehr ewig gültig. | Die Quelle gilt als getrennt, solange das C.M.I. nur andere Knoten sendet (gewollt). |
| R8 | Fünf Kleinigkeiten aus Task 3 (Neuverbindung, Logzeilen, 0 am Sitzungsende, Fehler im Callback, `held_k` beim Ablauf) kamen in die erste Korrekturrunde. | Sie betreffen die Sperre oder alten Zustand. | eine größere Korrektur |
| R9 | `thermal_mode()` entsteht in Task 5, die Strategie nutzt sie mit. | keine doppelte Moduslogik | keine |
| R10 | Der Echo-Alarm vergleicht mit `offset_sent_k`; seit R27/R30 meldet er auch ein fehlendes Echo, sobald das C.M.I. verbunden ist. | Nach dem Ende des Regelbetriebs gäbe es sonst einen dauernden Fehlalarm. | Ein Unterschied zwischen entschiedenem und gesendetem Wert fiele nicht auf (der Adapter hält beide gleich). |
| R11 | `thermal_mode` ist strenger als Entscheidung 11: Ist ein Bit vor dem ersten gesetzten unbekannt, heißt der Modus „unbekannt“. | kein falsches Vorzeichen | Versatz 0 in seltenen Zyklen mit Teildaten |
| R12 | Die Quelle im Store heißt `thermal.<name>`. | keine Kollision mit Wallbox, Victron oder HA | keine |
| R13 | Grund-Sollwert = Sollwert − Echo (Rückfall: gesendeter Wert). | Die UVR rechnet den Versatz in den Sollwert ein. | nur die Anzeige |
| R14 | Drei Kleinigkeiten aus Task 5 (Test für DEGRADED, Alarm nach der Übergabe, abgelaufene Freigabe) kamen in die Korrektur. | sicherheitsnah und klein | keine |
| R15 | Die Strategie: höchstens `max_offset_k/step_k` Stufen; die Wartezeit gilt in beide Richtungen; Akku und Autos nehmen sofort und dann je Intervall eine Stufe zurück; Netzbezug nach 300 s; ohne SOC kein neuer positiver Schritt; Gründe für 5.1 wie in der Tabelle des Betriebshandbuchs; Start mit (0, `thermal_off`). | Entscheidung 5 ließ das offen. | ein anderer Takt, sichtbar in den Szenarien |
| R16 | Eine höhere Regel, die nur hält, unterdrückt keine niedrigere, die den Betrag verkleinert. | Spec 18: „Zurück auf 0 geht immer sofort“ | Die Klimaanlage nimmt schneller zurück als die reine Rangfolge. |
| R17 | Vier Kleinigkeiten aus Task 6 kamen in die Korrektur. | klein | keine |
| R18 | Ein Auto zählt als „will mehr“, nur wenn es seinem Befehl folgt (Leistung bekannt und höchstens eine 1-A-Stufe darunter). | Die Formel des Plans war zirkulär. | Die Klimaanlage nimmt PV nur, wenn kein Auto mehr nehmen kann (die Rangfolge). |
| R19 | Der thermische Teil läuft in einem eigenen, geschützten Aufruf; ein Fehler darin nimmt die Freigabe zurück und meldet `control.thermal_exception`, Akku und Autos regeln weiter. | Akku und Autos dürfen nie blockiert werden. | ein zweiter Fehlerpfad |
| R20 | ein Test der Regelung ohne Klimaanlage | Rückwärtsverträglichkeit | keine |
| R21 | Ein Auto auf 0 A zählt als „will mehr“ (durch R23 c und R28 eingeschränkt). | Rangfolge Akku → Autos → Klima | siehe R23 |
| R22 | Das Szenario `klima-auto-vorrang` nutzt 4 kW Einspeisung statt 1,5 kW. | Mit 1,5 kW kann der e-Golf (Start bei 2760 W) nie starten; dieser Fall hat einen eigenen Variantentest. | keine |
| R23 | Funde aus dem Simulator (Task 12b): (a) Anlaufzeit nach Start oder Erhöhung, (b) Hysterese zwischen „folgt“ und „begrenzt selbst“, (c) ein Auto auf 0 A zählt nur, wenn Einspeisung + Leistung der Klimaanlage seine Startleistung erreichen. | Projektregel: Ein scheiterndes Szenario heißt zuerst Fehler in der Regelung. | In Randfällen nimmt die Klimaanlage Überschuss, den ein Auto hätte nutzen können. |
| R24 | `_Follow.limit_a`: Ein Auto, das sich selbst begrenzt hat, folgt erst wieder, wenn es bis auf eine 1-A-Stufe an diesen Strom herankommt. | Nur mit der Haltezeit pendelten Klimaanlage und Ladestrom alle ~20 min. | Ein Auto, das die Regelung unter diesem Strom hält, gilt bis zur nächsten Erhöhung, „Laden aus“ oder Abstecken als selbst begrenzt. |
| R25 | Anlaufzeit `RAMP_UP_S` = 90 s. | Der Tesla bestätigt nach bis zu 85 s. | Ein Tesla, der binnen 90 s selbst stoppt, gilt so lange als folgend. |
| R26 | Die Freigabe des CoE-Adapters gilt nur in einem Zyklus, der einen Versatz ≠ 0 entschieden hat; sonst wird sie sofort zurückgenommen. Eine 0 wird nie als „unverändert“ verworfen. | Gesamtreview: Die 0 wartete auf einen offenen Schreibvorgang; nach Paketverlust hielt der Adapter den alten Versatz. | zusätzliche 0-Pakete (unschädlich) |
| R27 | Live mit verbundenem C.M.I., aber ohne Echo ist die Klimaanlage nicht steuerbar: Versatz 0, kein Schreiben, nur die Warnung. | Gesamtreview: unbestätigte −0,5-K-Pulse und ein 0-Schreiben pro Minute | Bleibt das Echo aus, bleibt die Klimaanlage auf 0 (die Warnung zeigt es). |
| R28 | Der Vorrang der Autos zählt nur die Leistung der Klimaanlage, die sie freigeben kann (über ihrer Leistung bei Versatz 0, gelernt nach 5 min bei 0), mit Hysterese. | Gesamtreview: Die Grundlast hielt sonst ein Auto, das nie starten kann, vor der Klimaanlage. | Nach Start oder Moduswechsel zählt bis zu 5 min die volle Leistung. |
| R29 | Die Bestätigung „evcc abgeschaltet“ im Live-Dialog gilt wieder im Zweifel; ausgenommen sind nur `thermal.*` und Victron (R31). | sicherer als eine Liste der Präfixe | keine |
| R30 | Folgen von R26: Im ersten Zyklus steht eine Entscheidung 0 (`thermal_off`) im Log; die Prüfungen des Simulators übergehen Entscheidungen von 0. | 0 ist der sichere Zustand. | ein Logeintrag mehr beim Start |
| R31 | Victron bleibt von der evcc-Bestätigung ausgenommen (wie vor 6a). | R29 nannte nur `thermal.*`. | keine neue |
| R32 | Übergabe ohne Echo: Der Aktor bleibt in „Übergabe“, mit Regelbetrieb aus bleibt der Zustand DEGRADED, bis das Echo kommt. Nicht behoben. | Dafür bräuchte der core den Timeout der UVR; der Adapter sendet 0, die UVR fällt selbst zurück. | DEGRADED wird angezeigt, bis das Echo kommt. |

## Offene Kleinigkeiten

Keine davon ist sicherheitsrelevant; die Re-Reviews haben sie als „klein“ bewertet.

- **Konfiguration und CoE:** Digitale Kanäle nehmen `unit` und `scale` an; `unit` hat keinen
  Wertebereich. `offset_packet(inf/nan)` wirft Python-eigene Fehler statt eines deutschen Textes; die
  Bereichsprüfung des Rohwerts ist für Fließkommazahlen langsam; `decode` übergeht das Header-Byte 1.
  `set_offset` setzt `held_k` vor dem Senden (betrifft nur Fakes); `permit()` kann beim Zurücknehmen
  einen `OSError` werfen; die Logzeile „stale“ zeigt `retry_in_s=0.0`.
- **Safety:** Die Prüfung nur für den Versatz läuft für jeden Befehl; `SAME_K` und `_TOLERANCE`
  sind zwei Konstanten mit gleichem Wert.
- **Strategie:** Nach dem Ende eines Hinweises ist −2 → 0 → +1 in Folgezyklen möglich (Timer der
  Einspeisung nicht neu gestartet). Die Strategie merkt die letzte Erhöhung bei der Entscheidung, die
  Safety beim Senden (seltenes Ablehnen und Zurücksetzen). Nach einem Zurücksetzen lautet der Grund
  `thermal_off`, ein fehlendes Echo zeigt den Text von `thermal_stale`.
- **Vorrang der Autos (charging.py):** Ein schon als „begrenzt selbst“ beurteiltes Auto übersteht die
  Anlaufzeit (der Docstring verspricht mehr); ein „Laden an“ nach `forget` startet die Anlaufzeit;
  `_Follow.since` übersteht Zyklen ohne Werte; `unmet_surplus_w(spare_w=0.0)` ist als Standard
  ungünstig; die Anlaufzeit gilt je Wallbox, nicht je Auto; der Tesla-Wunsch im Fenster fließt
  nicht ein; ein schlafender Tesla ohne eigene Anfrage zählt nie; nach `forget` eines Autos kann die
  Klimaanlage einmal eine Stufe steigen. Die Schätzung der Grundlast (R28) kann einfrieren und ist im
  Dry-Run nicht sichtbar: `base_w` oder `spare_w` in Health oder im Log wäre hilfreich.
  `charging.py` hat 1645 Zeilen; `_Follow` und `_raises` könnten in ein eigenes Modul.
- **Planhinweis:** `HIGH_PERCENTILE_INDEX` steht in `shared/plan.py` und `app/planning/fallback.py`.
- **UI, HA, Telemetrie:** Das Minuszeichen U+2212 nur auf der Karte der Klimaanlage; E2E nur im
  Zustand „nicht verbunden“; `THERMAL_DEFAULT_LABEL` doppelt den Standard aus `CoeOffsetConfig`; die
  Prüfung auf Kollisionen in HA nur zwischen Fahrzeug und Klimaanlage; die Grafana-Abfrage der
  Leistung ohne Filter `phase='total'`; `fill(previous)` verlängert eine veraltete Versatz-Linie.
- **Werkzeuge und Simulator:** Im Punkt `thermal` läuft Schritt 4 auch nach einem gescheiterten
  Schritt 2; kleine Unschärfen in Testnamen und Pluralformen; `scenarios.py` hat rund 3500 Zeilen.
- **An der Anlage zu bestätigen:** Absender der Pakete (Docker-Masquerade, erwartet 192.168.82.20),
  Knotennummer und Einheit der Leistung beim ersten Empfang, die Zeiten (Echo in 1–2 s, 0 nach einem
  Ausfall), dass das C.M.I. jeden Wert binnen `stale_after_s` neu sendet; im Dry-Run beobachten, ob
  der Starttest der Autos (R23 c, R28) bei Wolken hin und her springt.
