# Phase 2c-2 – Akku und Spitzen: Design

**Stand: Entwurf vom 14.10.2026**, im Brainstorming mit dem Betreiber abgestimmt (Abschnitt 2).
Grundlage ist die Spec `docs/superpowers/specs/2026-10-08-bindaems-design.md`, vor allem 7.1, 7.4,
7.6 bis 7.8, 7.10, 8.3, 8.5 und 18, und das Design von 2c-1
(`docs/superpowers/specs/2026-10-12-phase-2c1-laderegelung-design.md`, „Design 2c-1“). Verweise wie
„Spec 7.6“ beziehen sich auf die Spec. Dieses Dokument präzisiert und ändert sie für die Pläne
2c-2a und 2c-2b; wo es abweicht, gilt es (Abschnitt 11.2). Die Hinweise für 2c-2 aus
`docs/uebergabe.md` („Hinweise für die Pläne 2c-2 und 2d“) sind eingearbeitet.

## 1. Ziel und Abgrenzung

2c-1 regelt die beiden Ladepunkte; Victron bekommt dort nur die Sicherwerte von sicherem Zustand
und Übergabe. **2c-2** bringt die übrigen Teile der Regelung aus Spec 7.1:

- **Akku-Sollwert** (Spec 7.6, 7.7): Der core setzt den flüchtigen Override des Netz-Sollwerts so,
  dass der Hausakku die Autos nur nach den Regeln der Akku-Unterstützung versorgt, und frischt ihn
  vor seinem Timeout auf.
- **15-min-Wächter** (Spec 7.4): misst den Netzbezug je Viertelstunde, rechnet ihn hoch und hält ein
  Ziel, wenn eins eingestellt ist.
- **Volle Zuordnungserkennung** (Spec 7.8): Der core erkennt selbst, welches Auto an welcher
  Wallbox steht, auch ein Gastfahrzeug.
- **Umbau** vorab: der Regelungs-Kern, die Aufteilung von `core/control/charging.py` und
  `core/runtime.py` und eine Regelung, die eine Ausnahme übersteht (Punkte 4 und 7 der Liste „Vor
  der Live-Schaltung“ in der Übergabe).

**Zwei Pläne** (Entscheidung 7):

- **2c-2a „Akku und Spitzen“:** Umbau, Akku-Sollwert, 15-min-Wächter, Simulator dafür. Danach kann
  Victron in den Dry-Run und, mit abgeschaltetem DESS, live.
- **2c-2b „Zuordnung“:** die volle Zuordnungserkennung samt Gastfahrzeug und Simulator dafür.

**Nicht in 2c-2:** Planausführung mit den Absichten `HALTEN` und `NETZLADEN` und der Optimierer
(Phase 3); damit auch keine Entladegrenze über den flüchtigen Override
`ess.override.maxdischargepower`. Das Budget aus der Monatsspitze kommt mit dem Leistungspreis im
Tarifmodell (Phase 3). Bedienung in UI und HA, auch die Auswahl des Gast-Modus (2d). Die
Tesla-Punkte der Liste „Vor der Live-Schaltung“ (Stundenbudget, Selbststart, Ansteckerkennung, 5 A,
Reserve des sicheren Zustands) bleiben für 2d.

## 2. Entscheidungen des Betreibers (14.10.2026)

1. **Victron in 2c-2, wie geplant.** Victron geht nach 2c-2a live, sobald Dynamic ESS aus ist; bis
   Phase 3 läuft der Akku dann ohne Preisoptimierung (Eigenverbrauch und die Regeln dieses Designs).
   Solange DESS an ist, ist Victron gesperrt (Spec 7.2, Stand Plan 2a); ein Dry-Run des Victron-Teils
   braucht deshalb DESS aus.
2. **Akku ins Auto:** wie Spec 7.6 in PV-Überschuss und Min+PV (ab SOC 70 %, Hysterese 5 %, höchstens
   3 kW, höchstens 15 min am Stück); Sofort lädt aus Netz und PV; **Boost darf auch aus dem Akku
   laden**, ohne SOC-Schwelle und ohne die Grenzen 3 kW und 15 min. Boost begrenzt nur der Schutz der
   USV-Reserve (Design 2c-1, 5.2).
3. **Sofort bleibt beim vollen Strom.** Was über die Grenzen des Overrides hinausgeht (8 kW,
   Importlimit), liefert der Akku.
4. **15-min-Wächter:** wird gebaut, mit einem Zielwert, Standard aus.
5. **Zuordnung:** Erkannt werden „Tesla an der EVCS“, „e-Golf am Wall Connector“ und ein
   Gastfahrzeug. Vom e-Golf liefert Home Assistant außer dem SOC nur „zu Hause“.
6. **Gast an der EVCS:** Sein Modus steht in der Absicht, Standard PV-Überschuss.
7. **Aufbau:** ein Regelungs-Kern mit eigenen Bausteinen (Abschnitt 4), Umsetzung in den zwei Plänen
   2c-2a und 2c-2b.

## 3. Absicht und Konfiguration

### 3.1 Absicht

Neu ist das Feld `guest` (`shared/control.py`, `PUT /v1/intent`):

| Feld | Werte | Standard |
|---|---|---|
| `guest.mode` | `pv`, `min_pv`, `now`, `off` | `pv` |

- Ein Gast ist kein Fahrzeug der Konfiguration; unter `vehicles` lehnt der core ihn weiter als
  unbekannt ab (422). Boost und Übersteuerung gibt es für ihn nicht.
- Das Feld kommt mit 2c-2a, denn schon der Akku-Sollwert braucht den Modus für eine Wallbox ohne
  zugeordnetes Fahrzeug (5.2); in 2c-2b regelt die EVCS einen erkannten Gast danach (7.4).
- Die Auswahl im UI kommt mit 2d; bis dahin setzt der Betreiber den Modus per `PUT /v1/intent`.

### 3.2 Konfiguration

Neue Felder mit Standardwerten (alle unter `control`, außer `home_entity`):

| Feld | Standard | Bedeutung |
|---|---|---|
| `control.support_soc_pct` | 70 | Akku-Unterstützung in PV-Überschuss und Min+PV ab diesem SOC (Spec 7.6) |
| `control.support_hysteresis_pct` | 5 | gesperrt wird sie erst unter Schwelle minus diesem Wert |
| `control.support_max_w` | 3000 | höchstens so viel Akkuleistung für die Autos in PV-Überschuss und Min+PV zusammen |
| `control.support_max_s` | 900 | längste Überbrückung am Stück |
| `control.setpoint_deadband_w` | 200 | Abstand, ab dem sich der Override ändert; darunter entsteht keiner (5.3) |
| `control.override_refresh_s` | 240 | so oft frischt der core einen aktiven Override auf; der Cerbo verwirft ihn nach 300 s |
| `control.peak_target_kw` | keiner | Ziel des 15-min-Wächters; ohne Wert ruht er |
| `control.peak_margin_kw` | 0,3 | Marge der Hochrechnung (Spec 7.4) |
| `vehicles.<name>.home_entity` | keiner | `device_tracker` in Home Assistant: `home` heißt zu Hause |

- Werte unter 0 lehnt die Konfiguration ab; `support_soc_pct` liegt zwischen 0 und 100,
  `override_refresh_s` über 0 und höchstens 280 s (20 s Reserve zum Timeout), `peak_target_kw`
  über 0.
- `home_entity` verlangt wie `soc_entity` einen Abschnitt `homeassistant`. Ein anderer Zustand als
  `home` heißt „nicht zu Hause“, `unknown` und `unavailable` heißen unbekannt.
- Der Schutz der USV-Reserve für den 15-min-Wächter nutzt das bestehende
  `control.reserve_guard_pct` (5).
- Keines der neuen Felder verlangt eine neue `config.ini` des Watchdogs (Betriebshandbuch 14): Er
  kennt nur USV-Reserve, sichere Ströme und Zeiten.

## 4. Aufbau und Regelzyklus

### 4.1 Regelungs-Kern und Bausteine

Alle Bausteine liegen rein in `core/control` (Importvertrag wie bisher).

| Baustein | Aufgabe |
|---|---|
| `regulation.py` (`Regulation`, neu) | der Kern: die einzige Schnittstelle der Laufzeit zur Regelung (`step`, `note_verdict`, `note_result`, `forget`, `idle`, `health`, `vehicle_wallbox`) |
| `peak_guard.py` (neu) | 15-min-Wächter (Abschnitt 6) |
| `battery.py` (neu) | Akku-Sollwert (Abschnitt 5) |
| `assignment.py` | Zuordnung; in 2c-2b mit der Erkennung (Abschnitt 7) |
| `charging.py` und die Module aus dem Umbau (4.4) | Laderegelung, Phasenwächter, Verteilung wie in 2c-1, dazu die Leistungsgrenze des 15-min-Wächters |

### 4.2 Reihenfolge im Zyklus

Der Kern rechnet je Zyklus in der Reihenfolge von Spec 7.1:

1. **Zuordnung:** in 2c-2a die vereinfachte aus 2c-1, in 2c-2b die Erkennung.
2. **Phasenwächter** wie in 2c-1.
3. **15-min-Wächter:** Netzbezug der Viertelstunde, Hochrechnung, verlangte Senkung. Er liefert der
   Laderegelung eine Leistungsgrenze und dem Akku-Sollwert eine Anforderung (6.3).
4. **Laderegelung:** wie in 2c-1, mit der Leistungsgrenze des Wächters.
5. **Akku-Sollwert:** aus den Ladeleistungen, den Modi, dem Stand der Unterstützung und der
   Anforderung des Wächters (Abschnitt 5).

Die Befehle kommen in dieser Reihenfolge: erst die der Ladepunkte nach Priorität, dann der Override.

### 4.3 Laufzeit

- Die Laufzeit spricht nur noch mit dem Kern. Die Verdrahtung der Regelung (Eingaben bauen, Urteile
  und Ergebnisse zurückgeben, Health) zieht aus `core/runtime.py` in ein eigenes Modul neben der
  Laufzeit.
- Die Ausführung bleibt, wie sie ist (Spec 7.1, Stand Plan 2c-1): erst die Befehle des Keepers, dann
  die der Regelung, alle durch einen gemeinsamen `SafetyContext` und den Ausführer. Jedes Urteil
  geht an den Kern zurück.
- Neu in den Eingaben: die Viertelstunde (6.1), der Override, wie der Cerbo ihn meldet, der
  Basis-Sollwert (`AcPowerSetPoint`), PV, Hauslast und die Sperren je Aktor.
- Das 15-min-Budget `grid_budget_w` im `SafetyContext` ist `P_zul` des Wächters (6.2), sonst
  `None`; es ist nie NaN oder ±∞.

### 4.4 Umbau (technische Schulden)

Zuerst, ohne das Verhalten zu ändern; alle Tests und Szenarien bleiben unverändert grün:

- `core/control/charging.py` (rund 1050 Zeilen) wird aufgeteilt: Ladepunkte und Verteilung, der
  Tesla-Teil, und die Merker der letzten eigenen Werte (`_LastValues`) als eigenes Modul, das Lade-
  und Akku-Regelung teilen.
- `core/runtime.py` (rund 1290 Zeilen) gibt die Verdrahtung der Regelung ab (4.3).
- Doppelte Hilfen (`_since`, `_number`, `PV_SURPLUS`) werden zusammengeführt.

### 4.5 Ausnahme in der Regelung

Wirft der Kern in einem Zyklus, ruht die Regelung in diesem Zyklus wie bei `idle`: Sie vergisst ihre
letzten Werte, und der nächste Zyklus beginnt neu. Dazu kommt der Alarm `control.exception`
(Fehler), solange der letzte Zyklus warf, und der Heartbeat fällt aus. Keeper, Stream, Alarme und
das Entscheidungslog laufen weiter. Das erledigt Punkt 4 der Liste „Vor der Live-Schaltung“: Bisher
brach eine solche Ausnahme den ganzen Zyklus ab.

## 5. Akku-Sollwert

### 5.1 Grundsatz

Der Override ist der gewollte Netzbezug. ESS regelt den Netzbezug darauf; alles andere gleicht der
Akku aus. Ohne Override gilt Eigenverbrauch: Der Akku deckt Haus und Autos, so weit er darf.

Spec 7.6 setzt den Override bei nicht erlaubter Unterstützung auf die ganze Ladeleistung der Autos.
Bei Sonne holte das auch den Teil aus dem Netz, den die PV deckt, und der Akku lüde dafür: Bei 6 kW
PV, 1 kW Haus und 5 kW Auto gäbe ein Override von 5 kW 5 kW Netzbezug, und der Akku lüde 5 kW. Der
core verschiebt deshalb nur den Teil, den sonst der Akku ins Auto liefert (Abweichung, 11.2).

### 5.2 Fehlbetrag und erlaubter Akku-Anteil

**Fehlbetrag:** Die PV deckt zuerst die Hauslast ohne Wallboxen, dann die Autos in der Reihenfolge
der Priorität, wie die Abrechnung (`core/accounting/flows.py`, `allocate`). Was einem Auto danach
fehlt, ist sein Fehlbetrag: die Leistung, die es aus Akku oder Netz bezieht. Gemessen wird an der
Wallbox (`Derived.wallbox_w`), ohne Wert an der Wallbox aus Tessie. Fehlt die PV, die Hauslast oder
die Leistung eines ladenden Autos, ist der gewollte Netzbezug unbekannt, und der Override bleibt,
wie er ist; er verfällt nach 300 s ohne Auffrischen, wenn das anhält (Zustand „unbekannt“ in
Health).

**Erlaubter Akku-Anteil** je Auto nach seinem geltenden Modus:

| Modus | aus dem Akku erlaubt |
|---|---|
| PV-Überschuss, Min+PV | wenn die Unterstützung gilt (unten): zusammen für diese Autos höchstens `support_max_w`, verteilt in der Reihenfolge der Priorität; sonst nichts |
| Sofort, Aus | nichts |
| Boost | alles (Entscheidung 2) |

- **Unterstützung gilt,** wenn der SOC mindestens `support_soc_pct` (70 %) erreicht hat und seitdem
  nicht unter `support_soc_pct − support_hysteresis_pct` (65 %) gefallen ist, und die Überbrückung
  noch keine `support_max_s` (900 s) dauert. Ein unbekannter SOC sperrt sie.
- **Überbrückung:** Sie beginnt, sobald ein Auto in PV-Überschuss oder Min+PV einen Fehlbetrag hat,
  und läuft, solange einer besteht. Sie endet, wenn 60 s lang keins mehr einen hat. Nach
  `support_max_s` ist die Unterstützung gesperrt, bis die Überbrückung endet. In PV-Überschuss
  senkt die Laderegelung dann ohnehin auf den echten Überschuss (Design 2c-1, 6.3); in Min+PV
  kommt der Mindeststrom dann aus dem Netz.
- **Ein Auto ohne Steuerung** zählt mit dem Modus seines Fahrzeugs bzw. des Gastes (2c-2b): der
  e-Golf oder ein Gast am Wall Connector. Bis 2c-2b, und solange die Zuordnung unklar ist, gilt für
  eine Wallbox der Modus des Fahrzeugs, das dort nach der Zuordnung steht; ohne eins der Modus des
  Gastes.
- **Victron vor den Wallboxen live:** Regelt bis 2d noch evcc die Ladepunkte, zählt trotzdem der
  Modus aus der Absicht von BindaEMS.

### 5.3 Gewollter Netzbezug und Override

1. **Gewollter Netzbezug** = Summe über die Autos von max(0, Fehlbetrag − erlaubter Akku-Anteil).
   Die Hauslast deckt der Akku immer.
2. **15-min-Wächter:** Verlangt er eine Senkung (6.3), sinkt der gewollte Netzbezug um sie, nie unter
   0, solange der SOC über USV-Reserve + `reserve_guard_pct` liegt. Die Schwelle der Unterstützung
   gilt dann nicht.
3. **Override** = Basis-Sollwert (`AcPowerSetPoint`, heute 0 W) + gewollter Netzbezug, auf 10 W
   gerundet.
   - Ein Override entsteht ab `setpoint_deadband_w` (200 W) gewolltem Netzbezug und fällt weg
     („kein Override“), sobald dieser unter die Hälfte (100 W) sinkt.
   - Dazwischen ändert er sich erst, wenn der neue Wert mindestens `setpoint_deadband_w` vom letzten
     eigenen Wert abweicht. Die Safety-Schicht verlangt nur 50 W; die 200 W halten das Rauschen der
     Hauslast vom Override fern.

### 5.4 Grenzen

- **Regel 2 der Safety-Schicht** (Spec 8.3, unverändert): Der Override ist nie negativ und höchstens
  min(`max_grid_charge_setpoint_w` (8 kW), Absicherung, 15-min-Budget). Was darüber hinausgeht,
  liefert der Akku.
- **Importlimit:** Über 20 A je Phase (rund 13,8 kW) liefert Victron ohnehin aus dem Akku (Peak
  Shaving). Die Rechnung berücksichtigt das nicht eigens; Regel 2 und das Importlimit wirken
  zusammen.
- **Sofort** bleibt beim vollen Strom (Entscheidung 3): Laden zwei Autos in Sofort zusammen mehr als
  8 kW, liefert der Akku den Rest. Wer das nicht will, hebt `max_grid_charge_setpoint_w` bis zum
  Importlimit an.
- **Regel 4** (Phasenvorhersage) verteilt eine Erhöhung des Overrides gleichmäßig auf drei Phasen
  und senkt sie, wenn eine Phase sonst über die Sicherung käme.

### 5.5 Befehle und Auffrischen

- Die Regelung schreibt nur den Override (`grid_setpoint`): einen Wert in W oder „kein Override“
  (`None`). Entladegrenze und Min-SOC schreibt weiter nur der Keeper.
- Wie die Laderegelung sendet sie nur Änderungen gegenüber ihrem letzten eigenen Wert (gemeinsame
  Merker, 4.4); Urteile und Ergebnisse setzen und löschen ihn wie in 2c-1. Senkungen sind normale
  Befehle, nicht dringend.
- **Auffrischen:** Ist ein eigener Override aktiv, sendet die Regelung ihn alle
  `override_refresh_s` (240 s) auf der Uhr der Safety-Schicht erneut, gezählt ab dem letzten Urteil
  mit Wert. Der Befehl trägt das neue Merkmal `Command.refresh`; die Safety-Schicht lässt dann den
  gleichen Wert durch (Abschnitt 8).
- Ein Auffrischen ist keine Änderung (Spec 7.10): Gelingt es (`OK`) oder ist es ein
  Dry-Run-Urteil, steht es nicht im Entscheidungslog. Abgelehnt, zurückgestellt oder gescheitert
  steht es dort wie jeder Befehl.
- **Erkennung fremder Änderungen:** Ein aufgefrischter gleicher Wert lässt die Erwartung, wie sie
  ist. Scheitert das Schreiben, verwirft der core die Erwartung wie bisher (Spec 7.9, Stand Plan
  2b); verschwindet der Override danach, ist das keine fremde Änderung.

### 5.6 Sperren, Zustände und Übergabe

- **Victron gesperrt** (DESS, ESS-Modus, BatteryLife, Ladefenster, fremde Änderung) oder nicht
  steuerbar: Der Akku-Sollwert sendet nichts und vergisst seine Werte, rechnet aber weiter für
  Health. Ein vorhandener Override verfällt nach 300 s von selbst; das ist der gewollte Rückfall.
  Ohne diese Regel stünde im Dry-Run neben DESS jede Sekunde eine Ablehnung im Log.
- **Regelung ruht** (Regelbetrieb aus, SAFE, START, OBSERVE): Übergabe und sicherer Zustand bleiben
  beim Keeper (Spec 8.5: kein Override, Entladegrenze unbegrenzt, Min-SOC = USV-Reserve), dazu der
  Watchdog am Cerbo, der auch den Override aufhebt.
- **Live** geht Victron nur mit `victron.live_allowed`, scharfem Watchdog (Spec 8.6) und ohne
  Sperre, wie jeder Aktor (Regel 8).

## 6. 15-min-Wächter

### 6.1 Messung

- Den Netzbezug der laufenden Viertelstunde (:00, :15, :30, :45 Wiener Zeit) gibt die Laufzeit aus
  der bestehenden Integration je Viertelstunde (`core/accounting`) an den Kern. So gibt es eine
  Quelle für die Energie je Viertelstunde, dieselbe wie für die Viertelstunden-Bilanz.
- Gezählt wird der positive Saldo über die Phasen (Spec 7.4): je Sekunde max(0, Netzleistung).
- Fehlen Messwerte länger als 10 s am Stück, ruht der Wächter bis zur nächsten Viertelstunde und
  meldet „lückenhaft“.
- **Boost** zählt außerhalb des Budgets (6.4): Seine Energie integriert der Wächter getrennt und zieht
  sie ab.

### 6.2 Ziel und Hochrechnung

- Ohne `control.peak_target_kw` ruht der Wächter ganz. Das Budget `B` ist bis Phase 3 der Zielwert;
  das Budget aus der Monatsspitze (Spec 7.4) kommt mit dem Leistungspreis.
- Zulässig ist im Rest der Viertelstunde die mittlere Leistung
  `P_zul = (B · 0,25 h − E_bisher) / t_rest − peak_margin_kw`.
- Verglichen wird mit dem mittleren Netzbezug der letzten 30 s (ohne Boost), nicht mit dem
  Sekundenwert. Liegt er über `P_zul`, ist der Unterschied die **verlangte Senkung**.
- `P_zul` geht als `grid_budget_w` an die Safety-Schicht (4.3). Ist das Budget aufgebraucht
  (`P_zul` < 0), drückt Regel 2 den Override auf 0.

### 6.3 Reaktion

In der Reihenfolge von Spec 7.4:

1. **Wallboxen drosseln,** in umgekehrter Priorität: erst Autos in PV-Überschuss, bis 0 (sie
   stoppen dann nach den Regeln ihres Modus), dann Min+PV und zuletzt Sofort, beide höchstens bis
   zum Mindeststrom; innerhalb einer Stufe zuerst das Auto mit der niedrigsten Priorität. „Geplant“
   gibt es erst mit Phase 4.
   - Die Laderegelung bekommt dafür je Auto eine Leistungsgrenze und behandelt sie wie die Grenze
     des Phasenwächters.
   - Eine Drosselung gilt bis zum Ende der Viertelstunde oder bis `P_zul` die gedrosselte Leistung
     um mindestens 1 kW übersteigt. So flattert der Tesla nicht und spart Tessie-Befehle.
2. **Akku stärker entladen:** Den Teil der verlangten Senkung, den die Wallboxen gemessen noch nicht
   bringen, gibt der Wächter als Anforderung an den Akku-Sollwert (5.3), solange der SOC über
   USV-Reserve + `reserve_guard_pct` liegt. So überbrückt der Akku auch, bis ein gedrosselter Tesla
   nach seiner 90-s-Regel folgt.
3. **Sonst** nimmt der Wächter die Überschreitung hin: Warnung `peak.exceeded` bis zum Ende der
   Viertelstunde, etwa „15-min-Ziel überschritten: 12,3 kW in der Viertelstunde bis 14:15.“

### 6.4 Letzte zwei Minuten und Boost

- In den letzten 120 s einer Viertelstunde startet keine Wallbox, deren Mindestleistung die
  Hochrechnung über das Budget höbe; der Start wartet auf die nächste Viertelstunde.
- **Boost** hebt das Ziel für sein Auto auf (Spec 7.4): Seine Leistung zählt nicht in die Hochrechnung,
  und gedrosselt wird es nie. Die geschätzten Mehrkosten zeigt erst das UI mit dem Leistungspreis.

## 7. Zuordnungserkennung (2c-2b)

### 7.1 Wer steht wo

Statt allgemeiner Hypothesen (Spec 7.8) bestimmt die Zuordnung je Wallbox mit laufendem
Ansteckvorgang (Design 2c-1, 8), **wer dort steht**: ein Fahrzeug der Konfiguration oder ein Gast.
Ein Fahrzeug steht höchstens an einer Wallbox. Ansteckvorgänge, Bindungen von Boost und
Übersteuerung und „geladen“ bleiben wie in 2c-1. Eine Übersteuerung geht der Erkennung vor, bis zum
Abstecken.

### 7.2 Indizien

Jedes Indiz zählt unabhängig von den anderen:

| Indiz | spricht für | schließt aus |
|---|---|---|
| Tessie: „zu Hause“ und „angesteckt“ | das Fahrzeug an jeder Wallbox mit laufendem Vorgang | das Fahrzeug an jeder Wallbox, wenn nicht angesteckt oder nicht zu Hause |
| Stromvergleich: Tessie-Ist-Strom und Phasenzahl gegen die Ströme einer ladenden Wallbox, 60 s lang | das Fahrzeug an dieser Wallbox, wenn sie auf 1 A passen | das Fahrzeug an dieser Wallbox, wenn beide laden und sie nicht passen |
| Zeitliche Korrelation: Der Vorgang begann zwischen Tessies letzter Meldung „nicht angesteckt“ und der ersten „angesteckt“, und kein anderer Vorgang begann in dieser Zeit | das Fahrzeug an dieser Wallbox | – |
| Phasen-Signatur beim Laden: Phasen mit mindestens 1 A, 60 s lang | ein Fahrzeug mit dieser Phasenzahl (`vehicles.<name>.phases`) oder einen Gast | jedes Fahrzeug mit anderer Phasenzahl |
| Home Assistant: e-Golf „zu Hause“ (`home_entity`) | das Fahrzeug an jeder Wallbox mit laufendem Vorgang | das Fahrzeug an jeder Wallbox, wenn nicht zu Hause |
| Ausschluss: Die andere Wallbox meldet kein Fahrzeug | ein angestecktes Fahrzeug mit Tessie an dieser Wallbox | – |

Unbekannte Werte sprechen für nichts und schließen nichts aus. Tessie fragt ein abgestecktes Auto nur
alle 600 s ab; deshalb misst die zeitliche Korrelation das Intervall zwischen zwei Meldungen statt
eines festen Fensters.

### 7.3 Entscheidung

- **Erkannt** ist ein Fahrzeug an einer Wallbox, wenn mindestens 2 Indizien es 60 s lang stützen und
  keins es ausschließt (Spec 7.8).
- **Gast** ist erkannt, wenn jedes Fahrzeug der Konfiguration an dieser Wallbox 60 s lang
  ausgeschlossen ist, etwa weil der e-Golf nicht zu Hause und der Tesla nicht angesteckt ist, oder
  weil die Phasen-Signatur zu keinem passt.
- **Angenommen:** Ohne genug Indizien steht an der Wallbox ihr Standardfahrzeug, mit diesem Vermerk;
  etwa wenn Tessie offline ist oder vor dem ersten Laden. Ist es ausgeschlossen, steht dort niemand
  Bekanntes: Bis zu einer Erkennung gilt die Wallbox als „Gast, angenommen“.
- **Unklar:** Widersprechen sich die Indizien 60 s lang ohne Unterbrechung, ist die Zuordnung
  unklar, und sie wird wieder klar nach 60 s ohne Widerspruch. Ein Widerspruch ist: ein Fahrzeug
  hat an zwei Wallboxen je 2 stützende Indizien, oder an einer Wallbox 2 stützende und eines, das es
  ausschließt. Dann gilt alles wie in 2c-1 (Spec 7.8, Stand Plan 2c-1): Tesla-Befehle pausieren, die
  EVCS senkt nur nach dem Phasenwächter.
- Das Bild „Tesla an der EVCS“ aus 2c-1 ist kein Widerspruch mehr, sondern eine Erkennung.

### 7.4 Folgen für die Regelung

| Wer steht wo | Regelung |
|---|---|
| Tesla an der EVCS | wie die Übersteuerung in 2c-1: der Tesla einmal je Vorgang auf `max_a`, danach regelt die EVCS in seinem Modus mit seinen Phasen und Grenzen |
| e-Golf am Wall Connector | nicht steuerbar: Last für Phasenwächter und 15-min-Wächter; für den Akku-Sollwert zählt sein Modus |
| Gast an der EVCS | die EVCS regelt im Modus `guest.mode` mit ihren eigenen Grenzen und allen Phasen ihrer `phase_map` |
| Gast am Wall Connector | Last; für den Akku-Sollwert zählt `guest.mode` |

Befehle, die aus einer neuen Erkennung folgen, tragen den Grund `assignment_detected`.

## 8. Safety-Schicht

- **`Command.refresh`** (neu, Standard falsch): Bei `grid_setpoint` meldet die Safety-Schicht für
  einen Befehl mit diesem Merkmal und gleichem Wert nicht `rate.unchanged`; alle anderen Schritte
  gelten unverändert, auch der Mindestabstand von 2 s, Sperren und Betriebsart. Bei anderen Arten
  hat das Merkmal keine Wirkung. Ein Auffrischen geht nie als dringend durch.
- **15-min-Budget:** `grid_budget_w` kommt vom Wächter (4.3); Regel 2 kennt es schon.
- Sonst bleiben alle Regeln, wie sie sind; die Property-Tests der Schicht decken den neuen Fall mit
  ab.

## 9. Entscheidungslog und Health

**Gründe** (neu):

| Grund (`codes`) | Text im Log | Bedeutung |
|---|---|---|
| `battery_comp` | Akku nicht ins Auto | Der Override verschiebt den Fehlbetrag der Autos aufs Netz. |
| `battery_support` | Akku-Unterstützung | Nur der Teil über der erlaubten Unterstützung geht aufs Netz. |
| `peak_guard` | 15-min-Wächter | Der Befehl folgt aus dem 15-min-Wächter (Drosselung oder Akku). |
| `assignment_detected` | Zuordnung erkannt | Der Befehl folgt aus einer neuen Erkennung (2c-2b). |

Boost trägt wie bisher `boost`.

**Eingaben** einer Entscheidung des Akku-Sollwerts, zusätzlich zu denen aus 2b: `pv_w`, `house_w`,
`cars_w` (Ladeleistung der Autos), `deficit_w` (Fehlbetrag zusammen), `allowed_w` (erlaubter
Akku-Anteil zusammen), `target_w` (gewollter Netzbezug) und `actual_setpoint_w` (Override laut
Cerbo; im Dry-Run unter ESS „kein Override“).

**Health:**

- `battery`: gewollter Netzbezug, letzter eigener Override, Stand der Unterstützung (gilt, gesperrt
  mit Grund: SOC, Zeit, Modus), Sekunden der laufenden Überbrückung, letztes Auffrischen, Sperre,
  „unbekannt“ bei fehlenden Werten.
- `peak`: Ziel, Energie bisher, Hochrechnung, `P_zul`, verlangte Senkung, Zustand (ruht, aktiv,
  überschritten, lückenhaft).
- je Wallbox (2c-2b): wer dort steht, Art (erkannt, angenommen, Übersteuerung, unklar) und die
  Indizien.

**Alarme** (neu): `control.exception` (Fehler, 4.5) und `peak.exceeded` (Warnung, 6.3).

## 10. Simulator und Tests

### 10.1 Modell

- **ESS** regelt den Netzbezug auf den Override, sonst auf den Basis-Sollwert; Peak Shaving am
  Importlimit bleibt.
- **Override:** Er verfällt 300 s nach dem letzten Schreiben. Mit DESS an verwirft das Modell ihn wie
  die Anlage, und die Selbstprüfung sperrt Victron.
- **Fahrzeuge:** der e-Golf „zu Hause“ oder „weg“ (Signal aus Home Assistant), ein Gastfahrzeug ohne
  Tessie mit 1 bis 3 Phasen, und jedes Auto lässt sich an jede Wallbox stecken.
- **Freigaben:** Ein Szenario nennt, welche Aktoren live sind. Die zehn bestehenden halten EVCS und
  Tesla live und Victron im Dry-Run; ihr Verhalten bleibt gleich.

### 10.2 Szenarien für 2c-2a (Victron live)

| Szenario | Prüfung |
|---|---|
| PV unter Wolken, SOC 60 % | Der e-Golf lädt außerhalb der Übergänge nicht aus dem Akku. |
| Min+PV ohne Sonne, SOC 80 % | Der Akku liefert höchstens 3 kW, nach 15 min nur noch das Netz. |
| Sofort am Abend, beide Autos | Der Akku liefert nur den Teil über 8 kW. |
| Boost am Abend | Der Akku darf liefern, bis zum Schutz der Reserve. |
| 1 h mit aktivem Override | Er verfällt nie, wird alle 240 s aufgefrischt, kein Alarm „fremde Änderung“. |
| DESS an | Victron bekommt keine Befehle, im Log keine Flut von Ablehnungen. |
| 15-min-Ziel 8 kW mit Lastspitzen | Keine Viertelstunde über dem Ziel; Drosselung in der Reihenfolge von 6.3; Boost nie gedrosselt. |
| PV mit schwerer Last auf einer Phase | Das Importziel bindet (Punkt 6 der Liste „Vor der Live-Schaltung“). |

### 10.3 Szenarien für 2c-2b

| Szenario | Prüfung |
|---|---|
| Tesla an der EVCS ohne Übersteuerung | erkannt binnen 2 min; `max_a` an den Tesla; die EVCS regelt in seinem Modus |
| e-Golf am Wall Connector | erkannt; keine Tesla-Befehle; als Last gezählt |
| Gast an der EVCS (3 Phasen), e-Golf weg | Gast erkannt; die EVCS lädt im Modus des Gastes |

Das bestehende Szenario `zuordnung` ändert sich bewusst: Das Bild „Tesla an der EVCS“ ist künftig
erkannt statt unklar. Dort prüft ein echter Widerspruch „unklar“.

### 10.4 Weitere Tests

- Jedes neue Modul entsteht mit Tests zuerst.
- Property-Tests für den Akku-Sollwert: nie negativ; nie mehr gewollter Netzbezug als der Fehlbetrag
  der Autos; mehr erlaubter Akku-Anteil ergibt nie mehr Netzbezug.
- Property-Tests für den 15-min-Wächter: `P_zul` nach der Formel; die Drosselung hält die
  Reihenfolge von 6.3 ein und lässt Boost aus.
- Die Abdeckungsgrenzen bleiben (≥ 90 % für `core/safety`, `core/control` und `core/actuators`).

## 11. Inbetriebnahme und Abweichungen von der Spec

### 11.1 Inbetriebnahme

1. **Nach 2c-2a:** Images ausrollen. Der Victron-Dry-Run braucht DESS aus; das Fenster dafür bestimmt
   der Betreiber.
2. **Vor Victron live:**
   - Prüfprotokoll Teil 2, Victron 17.2-4 „Kein Akku ins Auto“ (offen; es prüft genau den
     Mechanismus aus Abschnitt 5).
   - Die Watchdog-Probe mit vorher verstellten Victron-Werten (Übergabe, „Watchdog“).
   - `victron.live_allowed: true`.
3. **Nach 2c-2b:** Die Erkennung läuft im Dry-Run mit; Health zeigt, wer wo steht.
4. **Doku:** Betriebshandbuch (Akku-Sollwert, 15-min-Wächter, Zuordnung), Absätze „Stand Plan 2c-2“
   der Spec, Übergabe.

### 11.2 Abweichungen von der Spec

- **Spec 7.6, Mechanismus:** Der Override verschiebt nur den Fehlbetrag der Autos, nicht ihre ganze
  Ladeleistung (5.1). Die 3 kW gelten für alle Autos in PV-Überschuss und Min+PV zusammen.
- **Spec 7.6, Modi:** Boost darf aus dem Akku laden (Entscheidung 2).
- **Spec 7.4:** Das Budget ist bis Phase 3 der Zielwert. Der Akku überbrückt, bis die Drosselung der
  Wallboxen wirkt (6.3, Punkt 2).
- **Spec 7.8:** Die Hypothesen sind „wer steht wo“ je Wallbox (7.1). Vom e-Golf zählen nur „zu Hause“
  und die Phasen-Signatur. Der Modus eines Gastes steht in der Absicht (Entscheidung 6).
- **Spec 7.10:** Ein gelungenes Auffrischen steht nicht im Entscheidungslog (5.5).
- **Spec 8.3, Regel 5:** Ein Auffrischen des Overrides geht trotz gleichen Werts durch (Abschnitt 8).
- **Spec 18:** Victron geht nach 2c-2a live, wenn DESS aus ist; EVCS und Tesla wie bisher gemeinsam
  nach 2d (Design 2c-1, 14.2).
