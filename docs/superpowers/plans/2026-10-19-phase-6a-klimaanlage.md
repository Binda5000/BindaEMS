# Phase 6a – Klimaanlage: Implementierungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Die Klimaanlage (kühlt und heizt) nimmt PV-Überschuss auf, den Akku und Autos nicht brauchen, und hält sich bei teurem Strom und drohenden Spitzen zurück:
- BindaEMS liest über CoE V2 (UDP) vom C.M.I. Ist, Soll, Betriebsart, Rückmeldung, Leistung und das Echo des Versatzes.
- Es schickt der UVR610 einen Sollwertversatz von −3 … +3 K, zyklisch alle 30 s; die UVR fällt nach 5 min ohne Paket von selbst auf 0.
- Eine regelbasierte Strategie im core rechnet einen Leistungswunsch (mehr/0/weniger), erst der Aktor macht daraus das Vorzeichen je Betriebsart.
- Der Aktor `thermal.<name>` hat Dry-Run, Freigabe, Bestätigung über das Echo und Grenzen in der Safety-Schicht.
- UI, Home Assistant, InfluxDB und Entscheidungslog zeigen Versatz, Wunsch, Betriebsart und Gründe.
- Die Schnittstellen sind so angelegt, dass 6b (Wärmepumpe Solarfocus) nur eine neue Art in `thermal.devices`, einen Adapter und einen Aktor ergänzt.

**Architecture:** 6a ändert `shared`, core, app, UI, Simulator und Deploy.
- **`shared`:** `shared/coe.py` (neu, Codec), `ThermalConfig` und `CoeOffsetConfig` in `config.py`, `CommandKind.SETPOINT_OFFSET` und `thermal_actuator()` in `control.py`, `ThermalHint` und zwei Felder in `IntentPlanSlot` (`plan.py`), Gründe in `decisions.py`.
- **core:**
  - `core/adapters/coe.py` (neu): UDP-Endpunkt, Empfang, Zuordnung, Senden mit Sperre.
  - `core/actuators/thermal.py` (neu): Aktor `thermal.<name>`.
  - `core/control/thermal.py` (neu): Betriebsart, Beobachtung und Strategie (rein, ohne IO).
  - `core/safety/`: Familie `thermal`, Bereich, Schrittregel, sicherer Wert 0.
  - `charging.py`, `peak_guard.py`, `phase_guard.py`: lesende Zugriffe für die Strategie; `regulation.py`, `regulation_link.py`, `plan_exec.py`, `intent.py`, `runtime.py`: Einbau.
- **app:** Hinweis je Slot in `intent_plan`, Health-Durchreichung, HA-Sensoren, Telemetrie im Grafana-Dashboard.
- **UI:** Karte „Klimaanlage“ auf der System-Seite, Gründe im „Warum?“, Korrektur des Live-Dialogs.
- **Werkzeuge:** `python -m bindaems.tools.coe_probe` (mithören) und Punkt `thermal` in `verify_part2` (Prüfprotokoll Teil 2).
- **Simulator:** Modell der Klimaanlage und sechs Szenarien.
- **Deploy und Doku:** `5442/udp` am core, `docs/betrieb.md`, Prüfprotokoll Teil 2 Punkt `thermal`, `docs/verification/abnahme-phase-6a.md`.

**Tech Stack:** wie Plan 4b. Keine neuen Abhängigkeiten (CoE nur mit der Standardbibliothek: `struct`, `asyncio.DatagramProtocol`). Neuer, optionaler Abschnitt `thermal` in `config.yaml`.

**Spec:** `docs/superpowers/specs/2026-10-08-bindaems-design.md`, vor allem 1.1, 2, 5.3, 6.1, 7.3, 7.4, 7.9, 7.10, 8.2, 8.3, 8.5, 8.7, 11.2, 12, 13, 15 und 18 („Phase 6 – Wärme und Kälte“).
- Verweise wie „Spec 8.3“ beziehen sich darauf. Die Absätze „Stand …“ gehen dem älteren Text vor; die Präzisierungen unten gehen beiden vor.
- Grundlage der Gegenseite: die Vorgabe des Betreibers „Aufgabe: Klimaanlage (UVR610) ins EMS einbinden“ und `/mnt/project-files/waermepumpe/klimaanlage.md`.
- Vorgänger ist Plan 4b (`docs/superpowers/plans/2026-10-18-phase-4b-geplantes-laden.md`, auf `main` seit `7dabc56`). Seine Global Constraints und die der Pläne davor gelten weiter.

## Einordnung

- **Zeitpunkt:** Entscheidung des Betreibers vom 18.10.2026: 6a direkt nach 4b, vor Phase 5. Der Plan wird jetzt zur Freigabe geschrieben; umgesetzt wird nach der Freigabe.
- **Ausrollen:** 6a ändert den core. Es kommt wie 4b erst nach der Abnahme der Phase 3 an die Anlage, core und app gemeinsam, mit Regelbetrieb aus während des Wechsels. Der Aktor `thermal.klima` startet im Dry-Run; live erst nach Prüfprotokoll Teil 2, Punkt `thermal`, und dem Umschalten durch einen Admin (Spec 8.7).
- **Schon erledigt an der Anlage (Betreiber, 18.10.2026):** UVR sendet Echo (A12) und Leistung (A13, F51) als CAN-Ausgänge; C.M.I. 192.168.81.92 sendet CoE an 192.168.82.20; Timeout der UVR 5 min.
- **Danach:** Phase 5 (Lernen), dann 6b (Wärmepumpe, frühestens Herbst 2027).

## Vorgeschlagene Entscheidungen (zur Freigabe)

Der Plan ist auf diese Vorschläge geschrieben; eine andere Wahl ändert die genannten Tasks.

1. **Zuschnitt.**
   - **In 6a:** CoE-Codec, Empfang und Senden, Aktor mit Safety, regelbasierte Strategie im core, Hinweis je Slot aus der app, Health, UI-Karte, HA-Sensoren, Telemetrie, Probe-Werkzeug, Simulator, Betrieb, Prüfprotokoll und Abnahme.
   - **Nicht in 6a:** die Klimaanlage im Optimierer (Vorheizen/Vorkühlen über Stunden braucht ein Wärmemodell, Phase 5), die Klimaanlage als eigener Fluss in der Abrechnung (sie bleibt Hauslast; als Verbraucher in der Statistik legt der Betreiber sie über HA an), die Wärmepumpe (6b), ein Live-Schalter in HA (wie bei allen Aktoren nicht, Spec 12).
2. **Ein UDP-Endpunkt, ein Gerät** (Tasks 2, 3).
   - Der Adapter bindet `0.0.0.0:<listen_port>` (Standard 5442) und sendet vom selben Socket an `cmi_host:port`. Pakete von einer anderen Absender-IP als `cmi_host` werden verworfen (gezählt in Health, nicht geloggt je Paket).
   - 6a erlaubt höchstens ein Gerät der Art `coe_offset` (Prüfung in der Konfiguration). Ein zweites bräuchte einen gemeinsamen Endpunkt; das ist erst nötig, wenn es eines gibt.
3. **Senden: der Adapter hält den Versatz, der Aktor setzt ihn** (Tasks 3, 5).
   - Der Adapter sendet seinen gehaltenen Wert sofort nach jeder Änderung und dann alle `send_interval_s` (30 s), auch unverändert. Beim Start hält er 0 und sendet sofort 0; so hebt ein neu gestarteter core einen alten Versatz der UVR sofort auf.
   - **Sperre (zweite Sicherung neben der Safety-Schicht):** Der Adapter sendet seinen Wert nur, solange der core ihn in den letzten 10 s freigegeben hat (`permit`); sonst sendet er 0. Die Laufzeit gibt ihn je Zyklus frei, nur im Regelbetrieb (CONTROL, DEGRADED) und nur, wenn `thermal.<name>` wirksam live ist. Steht der Zyklus (Ausnahme, Hänger), fällt der Versatz nach 10 s auf 0, ohne auf die 5 min der UVR zu warten.
   - **Dry-Run** sendet damit zyklisch 0 (Spec 18, 6a): Der Ausführer ruft `send` im Dry-Run nicht, und die Sperre ist zu. Den gewünschten Versatz zeigt das Entscheidungslog.
4. **Leistungswunsch und Versatz** (Tasks 6, 8).
   - Der Wunsch ist eine ganze Stufe `w` von `−less_max_steps` bis `+max_steps` (`max_steps = max_offset_k / step_k`, Standard 6; `less_max_steps` Standard 2, also höchstens −1 K für „weniger“, damit der Komfort bleibt).
   - Versatz = `w · step_k · s` mit `s = +1` beim Heizen und `s = −1` beim Kühlen (Kühlen mehr = negativ, Heizen mehr = positiv). Bei `lueften`, `aus` oder `unbekannt` ist der Versatz 0.
5. **Strategie** (Task 6), je Zyklus, in dieser Rangfolge:
   1. **Sofort 0**, wenn: keine frischen Werte (Quelle nicht verbunden oder älter als `stale_after_s`), Betriebsart nicht `heizen`/`kuehlen`, Betriebsart gewechselt seit dem letzten Zyklus, Zustand DEGRADED, Aktor nicht steuerbar oder gesperrt.
   2. **Druck → höchstens 0:** 15-min-Wächter mit Reduktion (`reduction_kw > 0`) oder Zustand `exceeded`, oder Phasenwächter über `NONE`. Ein positiver Wunsch fällt sofort auf 0; ein negativer bleibt.
   3. **Vorrang der Autos:** Laden Autos im Modus PV oder Min+PV unter ihrem Wunsch (`unmet_surplus_w ≥ 230 W`, eine Stufe von 1 A), geht ein positiver Wunsch je `step_interval_s` um eine Stufe zurück.
   4. **Vorrang des Akkus:** Ist der SOC unter der Schwelle (`soc_end_pct` des Slots, ohne Plan `battery_soc_pct`, Standard 90) minus 5 Prozentpunkte, geht ein positiver Wunsch je Intervall um eine Stufe zurück; ein neuer positiver Schritt braucht SOC ≥ Schwelle. Mit dem Hinweis `mehr` (der Plan erwartet in diesem Slot Einspeisung, der Akku ist dann ohnehin voll) entfällt die Akku-Schwelle.
   5. **Mehr:** Einspeisung (`−grid_w`) ≥ `more_above_w` (Standard 800 W) ohne Unterbrechung seit `step_interval_s` (600 s) → eine Stufe mehr, höchstens `max_steps`.
   6. **Zurück:** Netzbezug ≥ `import_above_w` (Standard 300 W) ohne Unterbrechung seit 300 s bei positivem Wunsch → eine Stufe zurück.
   7. **Weniger:** Hinweis `weniger` und keine Einspeisung über 100 W → je Intervall eine Stufe weniger, bis `−less_max_steps`. Endet der Hinweis, geht der Wunsch sofort auf 0.
   8. **Sonst** bleibt der Wunsch stehen (Totband).
   - Jede Erhöhung des Betrags braucht `step_interval_s` seit der letzten Erhöhung; Verringerungen des Betrags gehen sofort (Spec 18: „Zurück auf 0 geht immer sofort“).
   - Alternative: ein PI-Regler auf die Einspeisung. Er bräuchte die Kennlinie der Anlage, die wir nicht kennen; die Stufen mit 10 min passen zur Vorgabe des Betreibers zum Verdichter.
6. **Rest-Überschuss** (Task 8). „Was sonst eingespeist würde“ ist die gemessene Einspeisung: Victron ESS lädt den Akku vor der Einspeisung, die Laderegelung gibt Autos den Überschuss im Sekundentakt. Was in 10 min noch eingespeist wird, haben Akku und Autos nicht genommen. Die Rangfolge Akku → Autos → Klima → Einspeisung (Betreiber, 18.10.2026) sichern zusätzlich die Regeln 5.3 und 5.4: Ein neues Auto oder ein leerer Akku drängt die Klimaanlage zurück.
7. **Hinweis je Slot aus der app** (Task 7).
   - `IntentPlanSlot.thermal: ThermalHint = "neutral"` mit `ThermalHint = Literal["mehr", "neutral", "weniger"]` und `IntentPlanSlot.soc_end_pct: float | None = None`.
   - `mehr`: der Slot plant Einspeisung ≥ 500 W (`export_w`). `weniger`: der Slot gehört zu den teuersten 20 % der Bezugspreise des Plans (80. Perzentil wie `expensive_today`) und plant Einspeisung < 100 W. Sonst `neutral`.
   - Ohne gültigen Slot gilt `neutral` und die feste Akku-Schwelle.
8. **Grenzen in der Safety-Schicht** (Task 4), je Gerät aus `config.yaml` (harte Grenzen, Spec 8.2):
   - Bereich `−max_offset_k … +max_offset_k`, `max_offset_k ≤ 3,0` (Schema). Werte werden auf `step_k` (0,5 K) gerundet; außerhalb wird geklemmt (Befund `range.clamped`).
   - **Schrittregel** (neu, `safety/offset.py`): Ein Befehl, der den Betrag gegenüber dem vorhandenen Wert (`present`, das Echo) erhöht, darf ihn um höchstens eine Stufe erhöhen und erst `step_interval_s` nach der letzten Erhöhung; ein Vorzeichenwechsel geht nur über 0. Sonst `offset.step` (abgelehnt). Verringerungen des Betrags und 0 sind immer erlaubt (`toward_safe`).
   - Ohne bekanntes Echo gilt 0 als vorhanden.
   - Im Dry-Run bleibt das Echo 0; dort prüft die Schrittregel gegen den zuletzt im Dry-Run entschiedenen Wert, sonst würde das Entscheidungslog jeden zweiten Schritt als abgelehnt zeigen.
   - Sicherer Wert und Rückgabewert ist 0 (`safe_commands`); kein Watchdog nötig (`needs_watchdog=False`), die UVR fällt selbst auf 0.
   - Die Zeit der letzten Erhöhung lebt nur im Speicher: Nach einem Neustart sendet der Adapter ohnehin 0.
9. **Bestätigung** (Task 5). Der Aktor liest `present` aus dem Echo (`thermal.<name>.offset_active_k`); bestätigt ist ein Wert, wenn das Echo ihn auf 0,05 K zeigt. `confirm_timeout_s = 60` (das C.M.I. sendet bei Änderung). Zeigt das Echo im Live-Betrieb länger als 120 s etwas anderes als den gehaltenen Wert, meldet der Aktor `thermal.echo_mismatch.<name>` (Warnung); den Ausfall nach drei Versuchen des Keepers meldet wie bei allen Aktoren `actuator.failed.thermal.<name>`.
10. **Werte und Signale** (Tasks 1, 3). Der Adapter schreibt, nach der Empfangstabelle in `config.yaml`:
    - `thermal.<name>.actual_c`, `.setpoint_c` (inkl. Versatz), `.offset_active_k`, `.power_w` (aus kW) als Messwerte;
    - `.cmd_on`, `.cooling`, `.venting`, `.heating`, `.running` als Zustände;
    - `.offset_sent_k` (der zuletzt gesendete Wert) als Zustand.
    - **Skalierung:** Einheit 1 (°C) und 7 (K) in Zehnteln, wie in der Vorgabe. Für jede andere Einheit gilt `scale` aus der Tabelle (Teiler, Pflicht). Die Leistung (Analog 4) steht im Beispiel mit `unit: 10` und `scale: 100`; beides prüft der erste Empfang mit `coe_probe listen` (Prüfprotokoll Teil 2). Ein Paket mit anderer Einheit als konfiguriert macht den Wert ungültig (`None`) und zählt in Health.
    - **Frische:** `stale_after_s` (Standard 180). Nach 180 s ohne gültiges Paket setzt der Adapter die Quelle auf „nicht verbunden“; damit werden auch die Zustände `stale`.
11. **Betriebsart** (Task 6), aus den Zuständen: `aus`, wenn `running` falsch; sonst `heizen` bei `heating`, `lueften` bei `venting`, `kuehlen` bei `cooling` (in dieser Reihenfolge, wie in der Vorgabe). Läuft die Anlage ohne gesetzte Betriebsart oder sind `running` oder alle drei unbekannt: `unbekannt`. Der Sollwert ohne Versatz ist `setpoint_c − offset_sent_k`.
12. **Anzeige** (Tasks 9, 10, 11).
    - System-Seite: Karte „Klimaanlage“ mit Betriebsart, läuft, Ist, Soll ohne Versatz, Versatz (gesendet und Echo), Wunsch, Leistung, letzter Empfang, Grund.
    - HA (je Gerät): `sensor` „Klima Versatz“ (K), `sensor` „Klima Betriebsart“ (enum), `sensor` „Klima Leistung“ (W), `sensor` „Klima Wunsch“ (Stufe).
    - InfluxDB: Messung `thermal` (Tag `id`, Felder `actual_c`, `setpoint_c`, `offset_k`, `offset_sent_k`, `wish`) alle 10 s; Leistung in `power` mit `source=thermal`. Grafana: ein Panel „Klimaanlage“.
13. **Deploy** (Task 13): `ems-core` veröffentlicht `5442:5442/udp`. C.M.I. (192.168.81.x) und VM (192.168.82.x) liegen in verschiedenen Subnetzen; Routing und Firewall für UDP 5442 in beide Richtungen sind ein Schritt des Betreibers (`docs/betrieb.md`).
14. **Werkzeuge** (Task 12).
    - `coe_probe listen` zeigt jedes Paket mit Absender, Knoten, Ausgang, Art, Einheit und Rohwert (für die Empfangstabelle und die Skalierung). Nur mit gestopptem core (der Port ist sonst belegt; das Werkzeug sagt das).
    - **Prüfprotokoll Teil 2, Punkt `thermal`** in `verify_part2` (wie `evcs`, `tesla`, `victron`, mit denselben Adapter- und Aktor-Klassen, Rückfragen und Bericht `…-pruefprotokoll-teil2-thermal.md`): (1) alle Werte der Empfangstabelle kommen an; (2) +0,5 K senden, das Echo zeigt es binnen 60 s, Rückfrage „Zeigt die Visu der UVR „EMS“ +0,5 K?“; (3) 0 senden, Echo 0; (4) mit Rückfrage: +0,5 K senden, dann nichts mehr senden, das Echo fällt binnen 5 min 30 s auf 0 (mit `--yes` übersprungen). Am Ende und bei Abbruch sendet es 0. Der core bleibt dabei gestoppt (gleicher Port).
15. **Simulator** (Task 12): Modell der Klimaanlage (Leistung folgt dem Versatz mit Verzögerung) und sechs Szenarien: `klima-pv-kuehlen`, `klima-auto-vorrang`, `klima-moduswechsel`, `klima-paketverlust`, `klima-spitze`, `klima-teuer-heizen`.
16. **Nichts geht von selbst live.** Der Aktor startet im Dry-Run, `live_allowed` ist im Beispiel aus.

## Präzisierungen gegenüber der Spec

1. **Codec** (`shared/coe.py`, rein, ohne IO):
   - `CoeValue(node: int, output: int, analog: bool, unit: int, raw: int)` (frozen dataclass; `output` 1-basiert wie in der UVR).
   - `encode(values: Sequence[CoeValue]) -> bytes`: Kopf `0x02 0x00 <4+8n> <n>`, je Wert `<node> <output−1> <1|0> <unit> <int32 LE>`; digital: Rohwert 0/1 im ersten Byte. 1 ≤ n ≤ 16, sonst `ValueError`.
   - `decode(data: bytes) -> list[CoeValue]`: `CoeError` bei falscher Version, Länge ≠ `4+8n`, `n = 0` oder `n > 16`.
   - `offset_packet(offset_k: float, *, node: int, output: int) -> bytes`: analog, Einheit 1, `round(offset_k · 10)`.
   - Prüfvektor: `encode([CoeValue(1, 2, True, 1, -102)])[4:] == bytes([1, 1, 1, 1, 0x9A, 0xFF, 0xFF, 0xFF])`.
2. **Konfiguration** (`shared/config.py`):
   - `CoeChannel(node: int 1..62, analog: int 1..64 | None, digital: int 1..64 | None, unit: int | None, scale: float > 0 | None)`; genau eines von `analog`/`digital`; `scale` Pflicht bei analog mit einer Einheit außer 1 und 7.
   - `CoeReceive`: `actual_c`, `setpoint_c`, `offset_active_k`, `cooling`, `venting`, `heating`, `running` (Pflicht), `power_kw`, `cmd_on` (optional).
   - `ThermalStrategyConfig`: `more_above_w: float = 800`, `import_above_w: float = 300`, `battery_soc_pct: float 0..100 = 90`, `less_max_steps: int 0..6 = 2`.
   - `CoeOffsetConfig`: `kind: Literal["coe_offset"]`, `label: str = "Klimaanlage"`, `cmi_host: IPv4Address`, `port: int = 5442`, `listen_port: int = 5442`, `own_node: int 1..62 = 60`, `own_output: int 1..64 = 1`, `max_offset_k: float 0.5..3.0 = 3.0`, `step_k: Literal[0.5] = 0.5`, `step_interval_s: float ≥ 60 = 600`, `send_interval_s: float 5..60 = 30`, `stale_after_s: float 60..290 = 180`, `receive: CoeReceive`, `strategy: ThermalStrategyConfig = ThermalStrategyConfig()`, `live_allowed: bool = False`. Prüfung: `max_offset_k` ist ein Vielfaches von `step_k`; `own_node` kommt in `receive` nicht vor („own_node ist auch ein Empfangsknoten“).
   - `ThermalDevice = Annotated[CoeOffsetConfig, Field(discriminator="kind")]` (6b ergänzt die Union). `ThermalConfig(devices: dict[str, ThermalDevice] = {})`, Name wie Wallboxen (`^[a-z][a-z0-9_]*$`). `Config.thermal: ThermalConfig = Field(default_factory=ThermalConfig)`. Prüfung: höchstens ein `coe_offset` („Phase 6a kennt nur eine Klimaanlage über CoE.“).
3. **Absicht und Plan** (`shared/control.py`, `shared/plan.py`): `CommandKind.SETPOINT_OFFSET = "setpoint_offset"` (nicht in `PERSISTENT_KINDS`); `THERMAL_PREFIX = "thermal."`, `thermal_actuator(name) -> str`; `ThermalHint`; `IntentPlanSlot.thermal`, `IntentPlanSlot.soc_end_pct`. Alte gespeicherte Absichten laden mit den Standardwerten.
4. **Health** (`HealthReport.thermal: dict[str, Any]`), je Gerät: `label`, `connected`, `last_packet_at`, `foreign_packets`, `bad_packets`, `mode`, `running`, `actual_c`, `setpoint_c`, `base_setpoint_c`, `offset_sent_k`, `offset_active_k`, `wish`, `power_w`, `reason` (Code), `hint`.
5. **Gründe** (`shared/decisions.py`, `_REASON_TEXT`):
   - `thermal_more`: „PV-Überschuss in die Klimaanlage“
   - `thermal_back`: „Netzbezug, Klimaanlage eine Stufe zurück“
   - `thermal_cars`: „Autos haben Vorrang“
   - `thermal_battery`: „Akku hat Vorrang“
   - `thermal_less`: „Teurer Strom, Klimaanlage zurückhaltend“
   - `thermal_peak`: „Spitze droht, Klimaanlage nicht mehr“
   - `thermal_mode`: „Betriebsart gewechselt, Versatz 0“
   - `thermal_stale`: „Keine Werte der Klimaanlage, Versatz 0“
   - `thermal_off`: „Klimaanlage aus oder lüftet, Versatz 0“
   - `_command_text` für `SETPOINT_OFFSET`: „Sollwertversatz {v} K“ (mit Vorzeichen, Komma, eine Nachkommastelle).

## Global Constraints

- Deutsch in Texten, Logs (ganze Sätze, `tests/unit/test_layout.py`), Commits im Stil `feat(core): …`; keine Modellbezeichnungen im Repo.
- `core` und `app` importieren nur aus `shared`; `core.safety` und `core.control` importieren nicht aus `core.adapters`, `core.actuators`, `core.runtime` (import-linter).
- Abdeckung ≥ 75 % gesamt, ≥ 90 % für `core/safety`, `core/control`, `core/actuators`.
- Prüfkette vor jedem Commit: `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src cerbo-watchdog/bindaems_watchdog`, `uv run lint-imports`, `uv run pytest -q --cov --cov-fail-under=75` plus der 90-%-Bericht; UI: `pnpm lint`, `pnpm check`, `pnpm test`; E2E bei UI-Änderungen. In der Cloud vor uv: `export UV_SYSTEM_CERTS=1; unset UV_NATIVE_TLS`.
- Versatz nie außerhalb ±3 K; die UVR begrenzt nicht.
- Kein Aktor ist live; nichts an der Anlage schalten. Werkzeuge senden nur auf ausdrücklichen Aufruf.
- Secrets nur über `BINDAEMS_*`; CoE braucht keine.

## Review Focus

1. **Paket von einer fremden IP oder kaputtes Paket** (zu kurz, falsche Länge, n = 0): verwerfen, zählen, Adapter läuft weiter → Tests in Task 3.
2. **Widersprüchliche Bits** (Heizen und Kühlen zugleich, läuft ohne Betriebsart): `heizen` nach der Reihenfolge bzw. `unbekannt` → Versatz 0 bei `unbekannt` → Tests in Task 6.
3. **core-Neustart oder hängender Zyklus mit gesetztem Versatz:** Start sendet 0; ohne `permit` binnen 10 s sendet der Adapter 0 → Tests in Task 3.
4. **Echo fehlt, aber Pakete kommen** (UVR-Ausgang nicht konfiguriert): Bestätigung läuft in den Timeout, kein Endlosschreiben, Alarm `thermal.echo_mismatch` → Tests in Task 5.
5. **Vorzeichenwechsel in einem Befehl** (+1 → −0,5 durch Moduswechsel und schnellen Wunsch): Safety lehnt ab, Strategie geht über 0 → Tests in Tasks 4 und 6.

---

### Task 1: Konfiguration und gemeinsame Typen

**Files:**
- Modify: `src/bindaems/shared/config.py`, `src/bindaems/shared/control.py`, `src/bindaems/shared/plan.py`, `deploy/config.example.yaml`
- Test: `tests/unit/shared/test_config.py`, `tests/unit/shared/test_control.py`, `tests/unit/shared/test_plan.py`

**Interfaces:**
- Produces: `CoeChannel`, `CoeReceive`, `ThermalStrategyConfig`, `CoeOffsetConfig`, `ThermalDevice`, `ThermalConfig`, `Config.thermal` (Präzisierung 2); `CommandKind.SETPOINT_OFFSET`, `THERMAL_PREFIX`, `thermal_actuator(name: str) -> str`; `ThermalHint`, `IntentPlanSlot.thermal`, `IntentPlanSlot.soc_end_pct` (Präzisierung 3).

- [ ] **Step 1: Write the failing tests**

```python
def test_thermal_defaults_empty(cfg_dict):
    cfg = Config.model_validate({**cfg_dict, "thermal": {}})
    assert cfg.thermal.devices == {}

def test_example_has_klima(cfg):
    dev = cfg.thermal.devices["klima"]
    assert dev.kind == "coe_offset" and str(dev.cmi_host) == "192.168.81.92"
    assert dev.own_node == 60 and dev.own_output == 1 and dev.max_offset_k == 3.0
    assert dev.receive.offset_active_k.analog == 3 and dev.receive.running.digital == 5
    assert dev.live_allowed is False

@pytest.mark.parametrize("bad", [{"max_offset_k": 3.5}, {"max_offset_k": 1.2}, {"own_node": 56}])
def test_coe_offset_rejects(example_thermal, bad): ...  # ConfigError / ValidationError

def test_only_one_coe_device(cfg_dict): ...  # "Phase 6a kennt nur eine Klimaanlage über CoE."
def test_channel_needs_exactly_one_of_analog_digital(): ...
def test_channel_scale_required_for_unit_10(): ...

def test_thermal_actuator():
    assert thermal_actuator("klima") == "thermal.klima"

def test_intent_plan_slot_defaults():
    slot = IntentPlanSlot(start=T0, intent=BatteryIntent())
    assert slot.thermal == "neutral" and slot.soc_end_pct is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/shared -k "thermal or coe or intent_plan_slot" -q`
Expected: FAIL (`AttributeError`/`ValidationError` on unknown key `thermal`)

- [ ] **Step 3: Implement the models of Präzisierung 2 and 3**

`deploy/config.example.yaml` bekommt den Abschnitt `thermal.devices.klima` mit der Empfangstabelle des Betreibers (Knoten 56; Analog 1–4, Digital 1–5; `power_kw` mit `unit: 10`, `scale: 100` und dem Kommentar „beim ersten Empfang mit coe_probe prüfen“) und `live_allowed: false`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/shared -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(shared): Konfiguration und Typen für die Klimaanlage (Phase 6a)"
```

### Task 2: CoE-Codec

**Files:**
- Create: `src/bindaems/shared/coe.py`
- Test: `tests/unit/shared/test_coe.py`, `tests/property/test_coe.py`

**Interfaces:**
- Produces: `CoeValue`, `CoeError(ValueError)`, `encode`, `decode`, `offset_packet` (Präzisierung 1); `scaled(value: CoeValue, channel: CoeChannel) -> float | bool | None` (analog: `raw / 10` bei Einheit 1 und 7, sonst `raw / channel.scale`; `None`, wenn `channel.unit` gesetzt ist und abweicht; digital: `bool(raw & 0xFF)`).

- [ ] **Step 1: Write the failing tests**

```python
def test_vector_from_coe_rs():
    assert encode([CoeValue(1, 2, True, 1, -102)])[4:] == bytes([1, 1, 1, 1, 0x9A, 0xFF, 0xFF, 0xFF])

def test_offset_packet():
    data = offset_packet(-1.5, node=60, output=1)
    assert data[:4] == bytes([2, 0, 12, 1])
    assert decode(data) == [CoeValue(60, 1, True, 1, -15)]

def test_digital_value_in_first_byte():
    assert decode(encode([CoeValue(56, 5, False, 43, 1)]))[0].raw == 1

@pytest.mark.parametrize("data", [b"", b"\x02\x00\x04\x00", b"\x01\x00\x0c\x01" + bytes(8), b"\x02\x00\x0d\x01" + bytes(8), b"\x02\x00\x0c\x02" + bytes(8)])
def test_decode_rejects(data):
    with pytest.raises(CoeError): decode(data)

def test_scaled_unit_mismatch_is_none(): ...
def test_scaled_temperature_tenths(): ...  # raw 240 → 24.0
```

Property test: `decode(encode(values)) == values` for 1–16 values with valid ranges.

- [ ] **Step 2: Run, expect FAIL** — `uv run pytest tests/unit/shared/test_coe.py tests/property/test_coe.py -q` (`ModuleNotFoundError`)
- [ ] **Step 3: Implement `shared/coe.py`** with `struct` (`"<BBBBi"`), no other imports besides `shared.config` for `CoeChannel`.
- [ ] **Step 4: Run, expect PASS**
- [ ] **Step 5: Commit** — `feat(shared): CoE-V2-Codec mit Prüfvektor`

### Task 3: CoE-Adapter (Empfang, Senden, Sperre)

**Files:**
- Create: `src/bindaems/core/adapters/coe.py`, `tests/fixtures/coe_cmi.bin` (ein Paket mit den 9 Werten des Betreibers)
- Modify: `tests/unit/core/adapters/conftest.py` (Fixture `coe_env`, `FakeDatagram`)
- Test: `tests/unit/core/adapters/test_coe.py`

**Interfaces:**
- Consumes: Task 1, Task 2.
- Produces:
  - `class DatagramPort(Protocol): def sendto(self, data: bytes, addr: tuple[str, int]) -> None; def close(self) -> None` and `PortFactory = Callable[[Callable[[bytes, tuple[str, int]], None], int], Awaitable[DatagramPort]]` (gets the receive callback and the listen port); real implementation `udp_port` with `loop.create_datagram_endpoint`.
  - `class CoeAdapter`: `__init__(self, name: str, cfg: CoeOffsetConfig, store: StateStore, port_factory: PortFactory = udp_port, *, clock: Callable[[], float] = time.monotonic, sleep=asyncio.sleep)`; `name`, `run()`, `health() -> AdapterHealth`; `set_offset(value_k: float) -> bool` (hält den Wert, sendet sofort; `False` ohne Sitzung); `permit(until_monotonic: float) -> None`; `held_k -> float`; `stats() -> dict[str, int | str | None]` (`last_packet_at`, `foreign_packets`, `bad_packets`, `unit_mismatch`).
  - Signals as in Entscheidung 10; source name = device name (`"klima"`), `register_source(name, cfg.stale_after_s)`.

- [ ] **Step 1: Write the failing tests**

```python
async def test_maps_packet_from_fixture(coe_env):
    env = coe_env(); await env.receive(FIXTURE, ("192.168.81.92", 5442))
    snap = env.store.snapshot()
    assert snap.ok("thermal.klima.actual_c") == 24.0
    assert snap.ok("thermal.klima.running") is True
    assert snap.ok("thermal.klima.power_w") == pytest.approx(1230.0)

async def test_ignores_foreign_ip(coe_env): ...      # no reading, stats()["foreign_packets"] == 1
async def test_bad_packet_counted_and_survives(coe_env): ...  # bad_packets == 1, next good packet mapped
async def test_unit_mismatch_makes_value_invalid(coe_env): ...
async def test_stale_after_180s_sets_disconnected(coe_env): ...  # ManualClock +181 s → quality STALE for running
async def test_sends_zero_on_start(coe_env):
    env = coe_env(); await env.start()
    assert env.port.sent[0] == (offset_packet(0.0, node=60, output=1), ("192.168.81.92", 5442))
async def test_resends_every_30s(coe_env): ...       # RecordingSleep: sends at 0, 30, 60 s
async def test_sends_zero_without_permit(coe_env):
    env = coe_env(); await env.start(); env.adapter.permit(env.mono() + 10)
    env.adapter.set_offset(-1.0); env.advance(11); await env.tick()
    assert decode(env.port.sent[-1][0])[0].raw == 0
async def test_set_offset_sends_immediately(coe_env): ...
async def test_offset_sent_signal(coe_env): ...      # thermal.klima.offset_sent_k == held value actually sent
```

- [ ] **Step 2: Run, expect FAIL** — `uv run pytest tests/unit/core/adapters/test_coe.py -q`
- [ ] **Step 3: Implement `core/adapters/coe.py`** following `evcs.py` (`Backoff`, `StatusLog`, `_set_connected`, `run()` loop). The session opens the port, starts a send loop (`send_interval_s`, sends `held_k` if `clock() < permit_until`, sonst 0) and a watch loop (every 10 s: `set_connected(False)` once `stale_after_s` passed without a valid packet; `set_connected(True)` and `store.touch` on every valid packet). Values via `store.update(f"thermal.{name}.{key}", value, source=name, kind=...)`.
- [ ] **Step 4: Run, expect PASS**
- [ ] **Step 5: Commit** — `feat(core): CoE-Adapter für die Klimaanlage`

### Task 4: Safety – Familie `thermal`

**Files:**
- Create: `src/bindaems/core/safety/offset.py`
- Modify: `src/bindaems/core/safety/limits.py`, `ranges.py`, `phases.py`, `layer.py`, `src/bindaems/core/control/actuator.py` (`_TOLERANCE`: 0,05 K)
- Test: `tests/unit/core/safety/test_offset.py`, `test_limits.py`, `test_ranges.py`, `test_layer.py`; `tests/property/test_safety_layer.py`; `tests/unit/core/safety/conftest.py` (`make_ctx` kennt `thermal.klima`)

**Interfaces:**
- Consumes: Task 1.
- Produces:
  - `Family` += `"thermal"`; `OffsetRange(max_k: float, step_k: float, interval_s: float)`; `HardLimits.offsets: Mapping[str, OffsetRange]` filled in `from_config` with `ActuatorSpec(id=thermal_actuator(n), family="thermal", label=dev.label, kinds=frozenset({SETPOINT_OFFSET}), live_allowed=dev.live_allowed, needs_watchdog=False)`.
  - `safe_commands` → `Command(a, SETPOINT_OFFSET, 0.0, urgent=True, reasons=("safe_state",))`; `toward_safe(kind, value, present)`: `value == 0` or `abs(value) <= abs(present)` with the same sign.
  - `check_range`: rounding to `step_k`, clamping with `range.clamped`.
  - `class OffsetSteps: check(command: Command, present_k: float | None, now: float) -> Finding | None; record(command: Command, present_k: float | None, now: float) -> None` (monotonic seconds; `offset.step` texts: „Versatz nur eine Stufe je {min} min weg von 0.“, „Vorzeichenwechsel nur über 0.“). The layer calls it after `check_range`, before `deadband`, with `ctx.present.get((actuator, SETPOINT_OFFSET))`.

- [ ] **Step 1: Write the failing tests**

```python
def test_catalog_has_thermal(limits):
    spec = limits.actuators["thermal.klima"]
    assert spec.family == "thermal" and spec.needs_watchdog is False

def test_range_clamps_and_rounds(limits):
    assert check_range(cmd(3.7), limits).value == 3.0
    assert check_range(cmd(-1.26), limits).value == -1.5

def test_step_one_away_from_zero():
    s = OffsetSteps(limits.offsets)
    assert s.check(cmd(0.5), present_k=0.0, now=0) is None
    s.record(cmd(0.5), 0.0, 0)
    assert s.check(cmd(1.0), present_k=0.5, now=599).code == "offset.step"
    assert s.check(cmd(1.0), present_k=0.5, now=600) is None
    assert s.check(cmd(1.5), present_k=0.5, now=900).code == "offset.step"  # two steps

def test_back_to_zero_always():
    assert OffsetSteps(limits.offsets).check(cmd(0.0), present_k=3.0, now=0) is None

def test_sign_change_rejected():
    assert OffsetSteps(limits.offsets).check(cmd(-0.5), present_k=1.0, now=10_000).code == "offset.step"

def test_safe_command_is_zero(limits): ...
def test_dry_run_checks_against_last_decided(): ...  # dry_run: 0.5 then 1.0 after 600 s accepted although echo is 0
def test_layer_accepts_zero_in_safe_state(...): ...  # urgent, toward_safe
```

Property test: extend `TARGETS`, `KINDS`, `in_range`, `safer`; oracle for the step rule.

- [ ] **Step 2: Run, expect FAIL**
- [ ] **Step 3: Implement** (Entscheidung 8).
- [ ] **Step 4: Run, expect PASS** — `uv run pytest tests/unit/core/safety tests/property/test_safety_layer.py -q`
- [ ] **Step 5: Commit** — `feat(safety): Grenzen und Schrittregel für den Sollwertversatz`

### Task 5: Aktor `thermal.<name>` und Einbau in die Laufzeit

**Files:**
- Create: `src/bindaems/core/actuators/thermal.py`
- Modify: `src/bindaems/core/runtime.py` (`build_adapters`, `build_actuators`, Freigabe je Zyklus, `_degraded_reasons`, Health), `src/bindaems/core/api/app.py` (`HealthReport.thermal`)
- Test: `tests/unit/core/actuators/test_thermal.py`, `tests/integration/test_runtime_thermal.py`

**Interfaces:**
- Consumes: Tasks 1, 3, 4.
- Produces:
  - `ThermalActuator(name: str, cfg: CoeOffsetConfig, set_offset: Callable[[float], bool], connected: Callable[[], bool])`, `id = thermal_actuator(name)`, `confirm_timeout_s = 60.0`; `present(snap) -> {SETPOINT_OFFSET: snap.ok(f"thermal.{name}.offset_active_k")}` (missing if not OK); `capabilities` not controllable with „Keine Werte der Klimaanlage.“ when the source is not connected; `send` → `set_offset`; `confirm` via `matches`; `alarms(now)` → `thermal.echo_mismatch.<name>` (Severity.WARNING) after 120 s mismatch while the last `send` value differs from the echo.
  - Runtime: per cycle `adapter.permit(monotonic() + 10)` only if state ∈ {CONTROL, DEGRADED} and `thermal.<name>` effectively live; degraded reason „Klimaanlage ohne Werte“ is **not** added (the strategy sets 0 itself; DEGRADED would stop the battery for an AC outage).

- [ ] **Step 1: Write the failing tests**

```python
def test_present_from_echo(snap_with): ...            # offset_active_k -1.0 → {SETPOINT_OFFSET: -1.0}
def test_confirm_within_tolerance(): ...               # -1.04 confirms -1.0
def test_send_returns_reason_without_session(): ...    # set_offset → False → REASON_NOT_ACCEPTED
def test_echo_mismatch_alarm_after_120s(): ...

async def test_runtime_permits_only_when_live(runtime_with_coe): ...
    # dry_run: adapter sends 0 although a command was decided (decision DRY_RUN in log)
async def test_runtime_handback_sends_zero(runtime_with_coe): ...
    # live → dry_run switch: keeper sends 0 urgent, owned released after echo 0
async def test_health_has_thermal_block(runtime_with_coe): ...
```

- [ ] **Step 2: Run, expect FAIL**
- [ ] **Step 3: Implement** (Entscheidungen 3 and 9). The fake port from Task 3 is injected through `CoreRuntime(adapters=[...])`; `build_actuators` finds the `CoeAdapter` by name like `_victron_write`.
- [ ] **Step 4: Run, expect PASS** — `uv run pytest tests/unit/core/actuators/test_thermal.py tests/integration/test_runtime_thermal.py -q`
- [ ] **Step 5: Commit** — `feat(core): Aktor für den Sollwertversatz der Klimaanlage`

### Task 6: Betriebsart und Strategie (rein)

**Files:**
- Create: `src/bindaems/core/control/thermal.py`
- Test: `tests/unit/core/control/test_thermal.py`

**Interfaces:**
- Consumes: Task 1.
- Produces:
  - `ThermalMode = Literal["heizen", "kuehlen", "lueften", "aus", "unbekannt"]`; `thermal_mode(snap: Snapshot, name: str) -> ThermalMode` (Entscheidung 11).
  - `@dataclass(frozen=True) ThermalObs`: `now: float` (monotonic s), `fresh: bool`, `mode: ThermalMode`, `degraded: bool`, `controllable: bool`, `export_w: float | None`, `import_w: float | None`, `soc_pct: float | None`, `soc_gate_pct: float`, `hint: ThermalHint`, `peak_pressure: bool`, `phase_pressure: bool`, `unmet_cars_w: float`.
  - `@dataclass(frozen=True) ThermalWish`: `steps: int`, `reason: str` (a code of Präzisierung 5).
  - `class ThermalStrategy: __init__(self, cfg: CoeOffsetConfig); step(self, obs: ThermalObs) -> ThermalWish; reset(self) -> None`.
  - `offset_for(wish: ThermalWish, mode: ThermalMode, step_k: float) -> float` (Entscheidung 4).

- [ ] **Step 1: Write the failing tests** (alle vier Kombinationen, Wechsel, Rangfolge; `obs()` is a helper with neutral defaults)

```python
@pytest.mark.parametrize(("mode", "steps", "offset"), [
    ("kuehlen", 2, -1.0), ("kuehlen", -2, 1.0), ("heizen", 2, 1.0), ("heizen", -2, -1.0)])
def test_offset_sign(mode, steps, offset):
    assert offset_for(ThermalWish(steps, "x"), mode, 0.5) == offset

def test_more_after_600s_export():
    s = ThermalStrategy(CFG)
    assert s.step(obs(now=0, export_w=900)).steps == 0
    assert s.step(obs(now=599, export_w=900)).steps == 0
    assert s.step(obs(now=600, export_w=900)) == ThermalWish(1, "thermal_more")

def test_export_interrupted_restarts_timer(): ...
def test_one_step_per_interval_up_to_max(): ...        # 6 steps after 3600 s
def test_mode_change_zero_immediately():
    s = at_steps(3, mode="kuehlen")
    assert s.step(obs(mode="heizen")) == ThermalWish(0, "thermal_mode")
def test_stale_zero(): ...                               # fresh=False → (0, "thermal_stale")
@pytest.mark.parametrize("mode", ["lueften", "aus", "unbekannt"])
def test_off_modes_zero(mode): ...
def test_peak_drops_positive_to_zero_keeps_negative(): ...
def test_cars_unmet_steps_back(): ...                    # unmet 230 W → one step back per interval
def test_battery_gate(): ...                             # soc 80 < 90: no new step; soc 84 < 85: step back
def test_hint_mehr_skips_battery_gate(): ...
def test_import_300w_for_300s_steps_back(): ...
def test_less_with_hint_until_minus_two(): ...           # hint weniger, export 0 → -1, -2 after 600 s each
def test_less_ends_with_hint(): ...                      # hint neutral → 0 immediately
def test_degraded_zero(): ...
def test_widersprüchliche_bits(): ...                    # running, heating and cooling → "heizen"; running without mode → "unbekannt"
```

- [ ] **Step 2: Run, expect FAIL**
- [ ] **Step 3: Implement** the order of Entscheidung 5; timers as monotonic seconds of the first qualifying cycle (`None` when interrupted).
- [ ] **Step 4: Run, expect PASS**; coverage of `core/control/thermal.py` ≥ 90 %.
- [ ] **Step 5: Commit** — `feat(core): Strategie für die Klimaanlage (Leistungswunsch)`

### Task 7: Hinweis je Slot aus der app

**Files:**
- Modify: `src/bindaems/shared/plan.py` (`intent_plan`), `src/bindaems/core/control/plan_exec.py` (`ActiveSlot`, `active_slot`, `plan_health`), `src/bindaems/core/control/intent.py` (`_plan_problems`: `soc_end_pct` 0..100)
- Test: `tests/unit/shared/test_plan.py`, `tests/unit/core/control/test_plan_exec.py`, `tests/unit/core/control/test_intent.py`

**Interfaces:**
- Consumes: Task 1.
- Produces: `thermal_hint(slot: PlanSlot, expensive_ct: float) -> ThermalHint`; `intent_plan` sets `thermal` and `soc_end_pct`; `ActiveSlot.thermal: ThermalHint = "neutral"`, `ActiveSlot.soc_end_pct: float | None = None`.

- [ ] **Step 1: Write the failing tests**

```python
def test_hint_mehr_on_planned_export(): assert thermal_hint(slot(export_w=600, import_ct=20), 30) == "mehr"
def test_hint_weniger_on_expensive_without_export(): assert thermal_hint(slot(export_w=0, import_ct=31), 30) == "weniger"
def test_hint_neutral(): ...
def test_intent_plan_carries_hint_and_soc(): ...      # 80. Perzentil über alle Slots des Plans
def test_active_slot_copies_hint(): ...
def test_old_intent_loads_neutral(): ...
```

- [ ] **Step 2: Run, expect FAIL**
- [ ] **Step 3: Implement** (Entscheidung 7; Perzentil wie `app/planning/fallback.py` `expensive_today`, `quantiles(n=10)[7]`; mit weniger als 4 Slots kein `weniger`).
- [ ] **Step 4: Run, expect PASS**
- [ ] **Step 5: Commit** — `feat(app): Hinweis für die Klimaanlage je Slot des Plans`

### Task 8: Strategie in der Regelung

**Files:**
- Modify: `src/bindaems/core/control/charging.py` (`unmet_surplus_w`), `peak_guard.py` (`pressure`), `phase_guard.py` / `charging.py` (`overload_level`), `regulation.py`, `src/bindaems/core/regulation_link.py`, `src/bindaems/core/runtime.py`, `src/bindaems/shared/decisions.py`
- Test: `tests/unit/core/control/test_regulation.py`, `tests/unit/core/control/test_charging.py`, `tests/unit/core/control/test_peak_guard.py`, `tests/unit/shared/test_decisions.py`

**Interfaces:**
- Consumes: Tasks 5, 6, 7 (`ActiveSlot.thermal`, `ActiveSlot.soc_end_pct`).
- Produces:
  - `ChargeController.unmet_surplus_w() -> float`: Σ über Ladepunkte im Modus PV oder Min+PV, angesteckt und geregelt, von `max(0, wanted_w − power_w)` des letzten `step`; 0 vor dem ersten `step`.
  - `ChargeController.overload_level() -> OverloadLevel`.
  - `PeakGuard.pressure() -> bool`: `state == "exceeded"` oder `reduction_kw > 0` im letzten `step`.
  - `RegulationInputs.thermal: Mapping[str, ThermalInputs]` with `ThermalInputs(fresh, mode, controllable, locked, mono_now)`; `Regulation` holds one `ThermalStrategy` per device, after the override: `ControlCommand(Command(thermal_actuator(n), SETPOINT_OFFSET, offset_for(...), reasons=(wish.reason,)), inputs={"thermal_steps": ..., "export_w": ..., "soc_pct": ...})`, only when the offset differs from the last requested one; `note_verdict`/`note_result` route `thermal.*`; `forget`/`idle` reset the strategy; `health()["thermal"]`.
  - Gründe und `_command_text` aus Präzisierung 5.

- [ ] **Step 1: Write the failing tests**

```python
def test_unmet_surplus_counts_pv_cars_only(...): ...
def test_peak_pressure_flag(...): ...
def test_regulation_emits_offset_after_override(reg_with_klima): ...   # export 900 W for 600 s, kuehlen → -0.5 K, reasons ("thermal_more",)
def test_regulation_no_repeat_same_offset(reg_with_klima): ...
def test_regulation_idle_resets(reg_with_klima): ...
def test_decision_text_offset():
    assert "Sollwertversatz −1,0 K" in decision_text("Klimaanlage", SETPOINT_OFFSET, -1.0, ...)
```

- [ ] **Step 2: Run, expect FAIL**
- [ ] **Step 3: Implement.** Export/import come from `Derived.grid_w` (negative = export); `soc_gate_pct` is `active.soc_end_pct` or `cfg.thermal…strategy.battery_soc_pct`; DEGRADED from the operating state passed through `RegulationLink.step`.
- [ ] **Step 4: Run, expect PASS** — `uv run pytest tests/unit/core/control tests/unit/shared -q`
- [ ] **Step 5: Commit** — `feat(core): Klimaanlage in der Regelung, nach Akku und Autos`

### Task 9: System-Seite und Live-Dialog

**Files:**
- Create: `ui/src/lib/system/ThermalCard.svelte`, `ui/src/lib/system/thermal.ts`
- Modify: `ui/src/lib/api/schemas.ts` (`CoreHealthSchema.thermal`, optional mit `{}`), `ui/src/routes/system/+page.svelte`, `ui/src/lib/system/view.ts` (`needsEvccConfirmation` nur für `wallbox.`/`vehicle.`), `ui/src/lib/decisions/…` bzw. `ui/src/lib/charging/view.ts` (`REASONS` um die Codes aus Präzisierung 5), `ui/src/lib/api/contract/system.json` (neu erzeugt)
- Test: `ui/src/lib/system/thermal.test.ts`, `ui/src/lib/system/view.test.ts`, `tests/integration/test_ui_contract.py`, E2E unter `ui/tests/e2e/` (Demo-Backend mit einer Klimaanlage)

**Interfaces:**
- Consumes: Health aus Task 5/7.
- Produces: `thermalRows(block): {label: string; value: string}[]` mit den Texten „Betriebsart“, „Läuft“, „Ist“, „Soll ohne Versatz“, „Versatz“ („−1,0 K (bestätigt)“ / „−1,0 K (Echo +0,0 K)“), „Wunsch“, „Leistung“, „Letzter Empfang“, „Grund“.

- [ ] **Step 1: Write the failing tests** — `needsEvccConfirmation(["thermal.klima"]) === false`; `thermalRows` for kühlen/−1,0 K/bestätigt and for „keine Werte“; contract test fails until regenerated.
- [ ] **Step 2: Run, expect FAIL** — `cd ui && pnpm test`
- [ ] **Step 3: Implement**; regenerate the contract with `UPDATE_UI_CONTRACT=1 uv run pytest tests/integration/test_ui_contract.py`.
- [ ] **Step 4: Run** `pnpm lint && pnpm check && pnpm test && pnpm e2e`, expect PASS
- [ ] **Step 5: Commit** — `feat(ui): Karte „Klimaanlage“ auf der System-Seite`

### Task 10: Home Assistant

**Files:**
- Modify: `src/bindaems/app/ha/entities.py` (`HaInputs.thermal`, `build_inputs`, `build_entities`), `src/bindaems/shared/config.py` (Objekt-IDs in der Prüfung auf Kollisionen)
- Test: `tests/unit/app/ha/test_entities.py`

**Interfaces:**
- Produces: je Gerät `thermal_<name>_offset` (sensor, „Klima Versatz“, K, `measurement`), `thermal_<name>_mode` (sensor, enum mit den fünf Betriebsarten), `thermal_<name>_power` (W, `power`), `thermal_<name>_wish` (Stufe). Unbekannt → `"None"`.

- [ ] **Step 1: Write the failing tests** — entities exist only with a configured device; values from a health fixture; discovery payload units.
- [ ] **Step 2–4: FAIL → implement → PASS** — `uv run pytest tests/unit/app/ha -q`
- [ ] **Step 5: Commit** — `feat(ha): Sensoren der Klimaanlage`

### Task 11: Telemetrie und Grafana

**Files:**
- Modify: `src/bindaems/core/telemetry/sampler.py` (`RULES`), `deploy/grafana/bindaems.json`
- Test: `tests/unit/core/telemetry/test_sampler.py`, `tests/unit/test_grafana.py`

**Interfaces:**
- Produces: Messung `thermal` (Tag `id`, Felder `actual_c`, `setpoint_c`, `offset_k`, `offset_sent_k` alle 10 s); `thermal.<n>.power_w` → `power` mit `source=thermal`, `id=<n>`, Feld `p_w`. Der Wunsch geht über das Entscheidungslog, nicht über Influx.

- [ ] **Step 1–4:** tests for `signal_to_point` of each new signal and the dashboard check (`test_grafana.py`) → FAIL → add rules and a panel „Klimaanlage“ (Versatz, Echo, Ist/Soll, Leistung) → PASS.
- [ ] **Step 5: Commit** — `feat(core): Telemetrie der Klimaanlage und Grafana-Panel`

### Task 12: Werkzeuge und Simulator

**Files:**
- Create: `src/bindaems/tools/coe_probe.py`, `src/bindaems/tools/part2/thermal.py`
- Modify: `src/bindaems/tools/verify_part2.py` (`--punkt thermal`, `--geraet NAME`, Standard das erste Gerät)
- Modify: `src/bindaems/tools/sim/model.py` (Klimaanlage), `src/bindaems/tools/sim/scenarios.py`, `src/bindaems/tools/sim/runner.py` (Fake-Port für den CoE-Adapter)
- Test: `tests/unit/tools/test_coe_probe.py`, `tests/unit/tools/part2/test_thermal.py`, `tests/unit/tools/part2/test_cli.py`, `tests/integration/test_sim_scenarios.py` (Liste `KLIMA`)

**Interfaces:**
- Produces:
  - `coe_probe listen [--sekunden N]` (Entscheidung 14); Punkt `thermal` in `verify_part2` mit den vier Schritten aus Entscheidung 14, getestet mit dem Fake-Port aus Task 3 (Echo kommt, Echo bleibt aus → FEHLER, Abbruch sendet 0); Ausgabe je Paket: „{zeit} von {ip}: Knoten {n}, analog {a}, Einheit {u}, Rohwert {r}“. Belegter Port → „Port 5442 ist belegt. Läuft der core noch?“ (Exit 2).
  - `PlantModel.thermal`: `mode`, `base_w` (Leistung ohne Versatz), `gain_w_per_k` (Standard 400), `tau_s` (300), `max_w` (3000); `power_w` folgt `base_w + gain · |Versatz in Richtung mehr|` erster Ordnung; zählt in `house_w` von L1–L3 zu gleichen Teilen. Das Modell beantwortet gesendete Versätze mit Echo und schickt alle 10 s ein Paket wie das C.M.I.
  - Szenarien (je mit `Checks`):
    - `klima-pv-kuehlen`: Sommer, Akku voll, Export 1,5 kW → Versatz geht in Stufen von −0,5 K mit ≥ 600 s Abstand, Export sinkt unter 800 W, nie unter −3 K.
    - `klima-auto-vorrang`: wie oben, nach 40 min steckt ein Auto im PV-Modus an → der Versatz geht stufenweise zurück, das Auto bekommt den Überschuss.
    - `klima-moduswechsel`: Kühlen mit −1,5 K, Wechsel auf Heizen → im selben Zyklus 0, kein Befehl mit falschem Vorzeichen.
    - `klima-paketverlust`: Pakete bleiben aus → nach 180 s Versatz 0 (Strategie), Adapter sendet 0.
    - `klima-spitze`: Heizen mit +1,0 K, 15-min-Wächter reduziert → sofort 0.
    - `klima-teuer-heizen`: Winterabend, Hinweis `weniger` → −0,5 K, nach 600 s −1,0 K, nicht weiter.
- [ ] **Step 1–4:** tests for argument parsing, output format and the busy port, the four `thermal` steps; scenario list in `test_sim_scenarios.py` → FAIL → implement → PASS (`uv run pytest tests/integration/test_sim_scenarios.py -k klima -q`).
- [ ] **Step 5: Commit** — `feat(tools): coe_probe und Simulator-Szenarien für die Klimaanlage`

### Task 13: Deploy, Betrieb, Prüfprotokoll, Abnahme, Spec

**Files:**
- Modify: `deploy/docker-compose.yml` (`ems-core.ports: ["5442:5442/udp"]`), `tests/unit/test_deploy_files.py` (core darf genau diesen Port veröffentlichen), `docs/betrieb.md` (neues Kapitel „Klimaanlage (Phase 6a)“ und die Kurzfassung oben), `docs/verification/README.md`, `docs/superpowers/specs/2026-10-08-bindaems-design.md` („Stand Plan 6a“ in 18, Phase 6; 8.2 „Wärmeerzeuger (je Gerät)“; 8.7 Reihenfolge; 11.2 Messung `thermal`; 12 Sensoren; 17.2 Punkt `thermal`)
- Create: `docs/verification/abnahme-phase-6a.md`
- Test: `tests/unit/test_deploy_files.py`; `docker compose -f deploy/docker-compose.yml config -q`

- [ ] **Step 1: Write the failing test** — `test_core_publishes_only_coe_port`: `svc["ports"] == ["5442:5442/udp"]`.
- [ ] **Step 2: Run, expect FAIL**
- [ ] **Step 3: Change compose and docs.**
  - `docs/betrieb.md`: Routing/Firewall UDP 5442 zwischen 192.168.81.0/24 und 192.168.82.0/24; `config.yaml`-Abschnitt; Empfang prüfen mit `coe_probe listen` (core gestoppt); Dry-Run beobachten; Live schalten; Gründe und Alarme; was bei „Keine Werte der Klimaanlage“ zu tun ist.
  - Reihenfolge an der Anlage: `coe_probe listen` (Knotennummer und Einheit der Leistung notieren, `config.yaml` anpassen), dann `verify_part2 --punkt thermal`, dann core starten, Dry-Run, live. `docs/verification/README.md` nennt den neuen Punkt.
  - `abnahme-phase-6a.md` (Spec 18, Abnahme 6a): Empfangstabelle, Timeout, 7 Tage Dry-Run ohne Fehler, im Live-Betrieb Versatz nie außerhalb ±3 K, höchstens eine Stufe je 10 min weg von 0, 0 spätestens einen Zyklus nach Moduswechsel oder veralteten Werten, jeder Versatz durch das Echo bestätigt; Auswertung aus dem Entscheidungslog und Influx.
- [ ] **Step 4: Run** the full chain, expect PASS.
- [ ] **Step 5: Commit** — `docs: Betrieb, Prüfprotokoll und Abnahme der Phase 6a`; danach `docs/uebergabe.md` nachführen (Ergebnis, Rulings, aufgeschobene Kleinigkeiten).

---

## Selbstprüfung (beim Schreiben erledigt)

- **Spec 18, 6a abgedeckt:** Protokoll und Prüfvektor (Task 2), Senden 30 s und Knoten 60 (Task 3), Grenzen und Schritte (Task 4), sofort 0 (Tasks 4, 6), Timeout der UVR als sicherer Zustand (Entscheidung 3, Task 5), Empfangstabelle konfigurierbar (Task 1), Quell-IP (Task 3), Status und Soll ohne Versatz (Task 6), Echo-Bestätigung (Task 5), Dry-Run sendet 0 (Tasks 3, 5), Port (Task 13), Anzeige (Tasks 9–11), Hinweis der app (Task 7), Rangfolge (Tasks 6, 8), Abnahme (Task 13).
- **Vorbereitung 6b:** `ThermalDevice` ist eine Union mit Diskriminator `kind`; Strategie und Wunsch kennen das Gerät nicht (`offset_for` ist die einzige Übersetzung); Familie `thermal` in der Safety-Schicht trägt je Gerät eigene Grenzen.
