# Standard-Lademodus je Ladepunkt – Implementierungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Je Ladepunkt (EVCS, Tesla Wall Connector) lässt sich ein Standard-Lademodus einstellen; wird dort ein Auto abgesteckt, stellt die app den Lademodus dieses Autos (bzw. des Gastes) automatisch darauf zurück.

**Architecture:** Nur app und UI. Die Einstellungen bekommen `wallbox_default_modes` (Ladepunkt → `ChargeMode`, ohne Eintrag kein Wechsel). Der `ChargingTracker`, der schon jede Session aus dem Steckzustand schließt, meldet jedes beobachtete Abstecken mit dem Fahrzeug der Session. Ein neuer Dienst `DefaultModes` setzt daraufhin über den `IntentService` `SetMode` bzw. `SetGuestMode` mit Quelle `system`; der core sieht nur eine gewöhnliche neue Absicht und bleibt unverändert.

**Tech Stack:** Python 3.12, pydantic, SQLAlchemy, FastAPI, pytest; Svelte 5, valibot, Vitest. Keine neuen Abhängigkeiten, keine Migration, keine ACL-Zeile.

**Spec:** `docs/superpowers/specs/2026-10-08-bindaems-design.md`, Abschnitt 7.5 (Lademodi), Abschnitt 12 (Bedienelemente „Laden“ und „Einstellungen“), „Stand Plan 4a“ (Sessions aus dem Steckzustand, `ChargingTracker`).

## Einordnung

- **Anlass (Betreiber, 28.10.2026):** „Ich hätte gerne noch eine Standard Option je Ladepunkt -> wenn ein Auto ab/abgesteckt wird wechselt es automatisch auf z.B.: PV-Überschuss.“
- **Heute:** Der Lademodus gehört dem Fahrzeug und bleibt stehen, bis du ihn änderst. Wer abends „Sofort“ wählt, lädt beim nächsten Anstecken wieder „Sofort“. Nur Boost und die Übersteuerung der Zuordnung enden schon heute beim Abstecken (core).
- **Was sich für dich ändert:** Unter Einstellungen → „Ladestationen“ steht je Ladepunkt neben dem Namen eine Auswahl „Nach dem Abstecken“ mit „Unverändert“ (Standard, so wie heute), „PV-Überschuss“, „Min+PV“, „Sofort“, „Aus“ und „Geplant“. Steckst du dort ein Auto ab, wechselt es auf diesen Modus; im Änderungsprotokoll steht der Wechsel mit „Standard-Lademodus“ als Urheber. Das ist das Verhalten des „Standard-Lademodus“ von evcc.
- **Ausrollen:** nur `ems-app` neu bauen und starten (`docker compose pull ems-app && docker compose up -d ems-app`), jederzeit, auch vor dem gemeinsamen Pull; der core bleibt, wie er ist. Wirkt erst, wenn du einen Ladepunkt auf einen Modus stellst. **Rückweg:** Eine ältere app kennt `wallbox_default_modes` nicht und fiele beim Lesen der Einstellungen auf die Standardwerte zurück (Tarif!). Das Betriebshandbuch bekommt deshalb den Abschnitt „Rollback auf einen Stand vor dem Standard-Lademodus“: vorher Einstellungen als YAML exportieren, den Schlüssel `wallbox_default_modes` löschen, nach dem Rollback importieren.
- **Parallel:** Keine offenen Branches; `main` ist `13f23b3` (mit Sofort-Limit und 5c).

## Entscheidungen (Vorschlag, zur Freigabe)

1. **Wann der Wechsel greift – Frage an dich.** Vorschlag: **beim Abstecken.** Gründe: Beim Abstecken weiß die app sicher, welches Auto dort war (das Fahrzeug der Session); ein Modus, den du vor dem Anstecken wählst („heute Sofort“), bleibt so stehen. Beim Anstecken dagegen kennt die app das Auto erst nach der Zuordnung (bis zu einigen Minuten „angenommen“ oder „unklar“), und eine Wahl kurz vor oder nach dem Anstecken würde überschrieben. Nachteil: Wechselt ein Auto zwischen den Ladepunkten, gilt der Standard des Ladepunkts, an dem es zuletzt abgesteckt wurde. Willst du stattdessen **„beim Anstecken“** oder **„beides“**, ergänzt Task 3 eine Regel: Der Wechsel kommt, sobald die Zuordnung des neuen Vorgangs „erkannt“ oder „Übersteuerung“ ist (höchstens 10 min nach dem Anstecken), und entfällt, wenn du den Modus dieses Autos in den 15 min vor dem Anstecken oder seither selbst geändert hast.
2. **Je Ladepunkt, in den Einstellungen.** Feld `wallbox_default_modes: dict[WallboxKey, ChargeMode]` in `RuntimeSettings`; ein fehlender Eintrag heißt „Unverändert“. Ein Schlüssel, den `config.yaml` nicht (mehr) kennt, wird ignoriert, wie bei `wallbox_names`. Kein Schlüssel in `config.yaml`.
3. **Wen es trifft:** das Fahrzeug der geschlossenen Session (`session_vehicle`, wie Statistik und Ladeübersicht). Ist es `guest`, wechselt der Gast-Lademodus. Ist es unbekannt oder kein Fahrzeug der Konfiguration, passiert nichts.
4. **Alle Modi wählbar**, auch „Aus“ und „Geplant“. Ein Gast kann nicht „Geplant“: Steht der Ladepunkt auf „Geplant“, bleibt der Gast-Lademodus unverändert. Ziel-SOC, Abfahrt und Sofort-Limit bleiben stehen; Boost und Übersteuerung enden wie bisher im core.
5. **Unabhängig von Regelbetrieb und Dry-Run:** Der Wechsel ändert nur die Absicht. Stimmt der Modus schon, sendet die app nichts (kein Eintrag im Protokoll). Urheber im Änderungsprotokoll `Standard-Lademodus`, Quelle `system`.
6. **Ausfälle:** War die app beim Abstecken aus, holt sie den Wechsel bei der ersten Beobachtung nach, in der der Ladepunkt leer ist, auch nach mehr als 30 min (die Session schließt dann bei `last_seen`). Ist der core gerade nicht erreichbar oder kommt ein Konflikt, versucht die app es bei jeder weiteren Beobachtung erneut, bis es klappt oder das Auto wieder irgendwo steckt. Lehnt der core ab, steht das im Log, ohne neuen Versuch.
7. **Fahrverhalten (5b):** Die Automatik setzt Ziel und Abfahrt nur in „Geplant“. Nutzt du sie für ein Auto, stell dessen Ladepunkt auf „Geplant“ oder „Unverändert“; mit „PV-Überschuss“ ruht sie nach jedem Abstecken. Der Wechsel des Modus gilt nicht als Bedienung von Ziel oder Abfahrt (keine Pause der Automatik).
8. **Kein Element in Home Assistant:** Der Standard wird selten geändert; der Modus je Auto in HA zeigt den Wechsel ohnehin. Auch keine Anzeige auf der Seite „Laden“ (das Änderungsprotokoll nennt den Grund).

## Global Constraints

- Texte für den Betreiber deutsch, Anrede „du“; Kommentare und Docstrings deutsch im Stil der Umgebung.
- Feldname `wallbox_default_modes` (Einstellungen, API, UI-Schema `wallboxDefaultModes` im Entwurf); Urheber `Standard-Lademodus`; UI-Text „Nach dem Abstecken“, Option „Unverändert“.
- Prüfkette vor jedem Commit: `ruff format --check`, `ruff check`, `mypy --strict` (src + cerbo-watchdog), `lint-imports`, `pytest` mit Abdeckung (≥ 75 % gesamt, ≥ 90 % safety/control/actuators), UI `pnpm check`, `pnpm lint`, `pnpm test`. In der Cloud vor uv: `export UV_SYSTEM_CERTS=1; unset UV_NATIVE_TLS`.
- Commits deutsch im Stil `feat(app): …`. Kein Aktor wird live geschaltet; der core bleibt unverändert.

## Review Focus

- Kurzes Wackeln des Steckzustands (EVCS meldet einmal `status` 0 und dann wieder verbunden): Heute schließt das schon eine Session; der Wechsel kommt dann einmal, das ist gewollt. Ein unbekannter Zustand (`None`) löst nichts aus → Test in Task 2 (`test_unknown_plug_state_reports_nothing`).
- Abstecken, während die app aus war, länger als `SESSION_GAP` → genau ein Wechsel nach dem Start; ein Auto, das nach der Pause noch steckt, wechselt nicht → Tests in Task 2.
- Core nicht erreichbar beim Abstecken: Wechsel kommt bei der nächsten Beobachtung; steckt das Auto bis dahin wieder an, kommt er nicht → Tests in Task 3.
- Du wählst den Modus selbst, nachdem der Wechsel gescheitert ist und bevor er nachgeholt wird: Deine Wahl geht vor → Test in Task 3 (`test_manual_mode_after_unplug_wins_over_retry`).
- Ladepunkt auf „Geplant“, Gast steckt ab: kein Befehl, keine Ausnahme → Test in Task 3.

---

### Task 1: Einstellung `wallbox_default_modes`

**Files:**
- Modify: `src/bindaems/shared/settings.py` (`RuntimeSettings` ~302–311)
- Modify: `ui/src/lib/api/contract/settings.json` (Vertrag, falls der Vertragstest ihn neu erzeugt verlangt)
- Test: `tests/unit/shared/test_settings.py`

**Interfaces:**
- Produces: `RuntimeSettings.wallbox_default_modes: dict[WallboxKey, ChargeMode] = Field(default_factory=dict)` mit Kommentar „Lademodus nach dem Abstecken je Ladestation (Schlüssel wie in config.yaml); ohne Eintrag bleibt der Modus“.

- [ ] **Step 1: Tests schreiben** – `test_default_modes_empty_by_default` (`RuntimeSettings().wallbox_default_modes == {}`), `test_default_modes_accept_every_mode` (`{"evcs": "pv", "twc": "planned"}` → `ChargeMode.PV`, `ChargeMode.PLANNED`), `test_default_modes_reject_unknown_mode` (`{"evcs": "boost"}` → `ValidationError`), `test_stored_settings_without_default_modes_load` (Dump ohne den Schlüssel validiert).
- [ ] **Step 2: Tests laufen lassen, sie scheitern** – `uv run pytest tests/unit/shared/test_settings.py -k default_modes -v` → FAIL (`extra_forbidden` bzw. Attribut fehlt).
- [ ] **Step 3: Feld einbauen** (Import `ChargeMode` aus `bindaems.shared.control`).
- [ ] **Step 4: Tests und Vertragstests grün** – `uv run pytest tests/unit/shared/test_settings.py tests/integration -k "settings or contract" -q`; ist der API-Vertrag veraltet, ihn mit dem Werkzeug neu schreiben, das der scheiternde Test nennt.
- [ ] **Step 5: Commit** – `feat(shared): Standard-Lademodus je Ladestation in den Einstellungen`

### Task 2: Der Tracker meldet das Abstecken

**Files:**
- Modify: `src/bindaems/app/charging/tracker.py` (`record`, `_track`, `_close`, `run`; neue Klasse `Unplugged`)
- Test: `tests/unit/app/charging/test_tracker.py`

**Interfaces:**
- Produces: `@dataclass(frozen=True) class Unplugged: wallbox: str; vehicle: str | None; at: datetime` (`vehicle` ist das Fahrzeug der Session, `None` statt `""`).
- Produces: `ChargingTracker.record(now, seen) -> list[Unplugged]` (bisher `None`).
- Produces: `ChargingTracker(engine, clock, cfg, live, on_observed: Callable[[list[Unplugged], list[WallboxSeen]], Awaitable[None]] | None = None)`; `run` ruft ihn nach jedem erfolgreichen `record` (auch mit leerer Liste, damit Task 3 nachholen kann), ein Fehler darin kommt ins Log.

Regeln:
- `_track` mit `connected is False` und offener Session → ein `Unplugged(wallbox, vehicle, now)`.
- Schließt `record` eine Session wegen `SESSION_GAP` und zeigt dieselbe Beobachtung den Ladepunkt mit `connected is False` → ein `Unplugged(wallbox, vehicle, current.last_seen)`. Steckt das Auto noch (neue Session), oder ist der Zustand unbekannt → keins.
- Scheitert die Transaktion, gibt es keine Meldung (die Ausnahme geht weiter wie heute); der nächste Lauf liest die offenen Sessions neu und meldet dann.

- [ ] **Step 1: Tests schreiben** – `test_unplug_reports_session_vehicle` (Tesla an `twc`, dann `connected=False` → `[Unplugged("twc", "tesla", t1)]`), `test_unplug_of_guest_reports_guest`, `test_unplug_without_known_vehicle_reports_none` (Session ohne Belegung → `vehicle is None`), `test_unknown_plug_state_reports_nothing` (`connected=None` → `[]`, Session offen), `test_unplug_after_gap_reports_once_at_last_seen` (Lücke 45 min, dann leer → ein Eintrag mit `at == last_seen`; der nächste Lauf → `[]`), `test_still_plugged_after_gap_reports_nothing`, `test_run_passes_reports_and_observation_to_callback` (Rückruf bekommt Meldungen und `seen`; ein Fehler im Rückruf stoppt `run` nicht).
- [ ] **Step 2: Tests laufen lassen, sie scheitern** – `uv run pytest tests/unit/app/charging/test_tracker.py -v` → FAIL.
- [ ] **Step 3: Umsetzen** – `_close`/`_finish` geben das Fahrzeug zurück; `record` sammelt die Meldungen; Docstring des Moduls um einen Spiegelstrich „Abstecken melden“ ergänzen.
- [ ] **Step 4: Tests grün** – `uv run pytest tests/unit/app/charging -q` → PASS (bestehende Tests unverändert grün).
- [ ] **Step 5: Commit** – `feat(app): Tracker meldet das Abstecken mit dem Fahrzeug der Session`

### Task 3: Dienst `DefaultModes` und Verdrahtung

**Files:**
- Create: `src/bindaems/app/charging/default_mode.py`
- Modify: `src/bindaems/app/runtime.py` (~365: Tracker mit `on_observed=self.default_modes.observed`)
- Test: `tests/unit/app/charging/test_default_mode.py`, `tests/integration/test_app_default_mode.py` (Muster: bestehende Integrationstests der app mit Fake-core, z. B. die von Sofort-Limit)

**Interfaces:**
- Consumes: `Unplugged`, `WallboxSeen` (Task 2); `RuntimeSettings.wallbox_default_modes` (Task 1); `IntentService.apply(change, *, actor, source, precondition=None)`, `IntentService.current()`, `SetMode`, `SetGuestMode`, `CoreDown`, `ConflictAgain`, `Rejected`, `PreconditionFailed` aus `app/control/intent.py`; `GUEST` aus `app/charging/sessions.py`; `CurrentSettings` aus `app/settings/service.py`.
- Produces: `ACTOR: Final = "Standard-Lademodus"`; `class DefaultModes: __init__(self, intents: IntentService, settings: CurrentSettings, cfg: Config)`, `async def observed(self, unplugged: Sequence[Unplugged], seen: Sequence[WallboxSeen]) -> None`.

Regeln (Entscheidungen 3 bis 6):
- Je Meldung: Standard des Ladepunkts aus den **aktuellen** Einstellungen; fehlt er → nichts. Ziel ist das Fahrzeug (nur Schlüssel aus `cfg.vehicles`) oder bei `GUEST` der Gast; „Geplant“ für den Gast → nichts.
- Offen bleibt je Ziel höchstens ein Wunsch (`dict[str, ChargeMode]`, Schlüssel Fahrzeug oder `GUEST`); eine neue Meldung für dasselbe Ziel ersetzt ihn.
- Bei jedem Aufruf: Wünsche verwerfen, deren Ziel in `seen` wieder an einem Ladepunkt mit `connected is True` steht. Dann jeden Wunsch einmal versuchen: Modus aus `intents.current()` (`vehicles.get(key, VehicleIntent()).mode` bzw. `guest.mode`); gleich → erledigt; sonst `apply(SetMode | SetGuestMode, actor=ACTOR, source="system", precondition=…)`, wobei die Vorbedingung prüft, dass der Modus seit dem Lesen unverändert ist. Erfolg, `PreconditionFailed` (jemand hat bedient, Bedienung geht vor) und `Rejected` (Log) → erledigt; `CoreDown`, `ConflictAgain` → bleibt offen.
- Ein Wunsch, der beim Abstecken nicht ging, und eine Bedienung des Modus danach: Die Bedienung geht vor. Dafür merkt sich der Wunsch den Modus des Ziels beim Abstecken (aus `intents.cached()`, `None` ohne Absicht); weicht der gelesene Modus beim Nachholen davon ab, ist er erledigt. (Die Version der Absicht taugt dafür nicht, der Plan erhöht sie laufend.)
- Log `info` „Standard-Lademodus gesetzt“ mit `wallbox`, `vehicle`, `mode`.

- [ ] **Step 1: Unit-Tests schreiben** (Fake `IntentService` mit Absicht und Aufrufliste) – `test_unplug_sets_wallbox_default_mode` (Tesla in „Sofort“ an `twc`, Standard `pv` → ein `SetMode("tesla", PV)` mit `actor="Standard-Lademodus"`, `source="system"`), `test_no_default_means_no_change`, `test_same_mode_sends_nothing`, `test_guest_unplug_sets_guest_mode`, `test_planned_default_leaves_guest_unchanged`, `test_unknown_or_foreign_vehicle_is_ignored`, `test_core_down_retries_on_next_observation`, `test_replugged_vehicle_drops_pending_change`, `test_manual_mode_after_unplug_wins_over_retry`, `test_rejected_change_is_logged_not_retried`, `test_settings_read_at_unplug_time` (Standard geändert zwischen zwei Abstecken → jeweils der aktuelle).
- [ ] **Step 2: Tests laufen lassen, sie scheitern** – `uv run pytest tests/unit/app/charging/test_default_mode.py -v` → FAIL (Modul fehlt).
- [ ] **Step 3: `default_mode.py` umsetzen** (Modul-Docstring mit den Regeln oben).
- [ ] **Step 4: Verdrahten in `runtime.py`** (`self.default_modes = DefaultModes(self.intents, self.settings, cfg)` vor dem Tracker).
- [ ] **Step 5: Integrationstest** – `test_unplug_switches_mode_to_wallbox_default`: app mit Fake-core, Einstellung `{"twc": "pv"}` über die Einstellungs-API, Tesla in „Sofort“ angesteckt, dann Health mit `vehicle_connected=false` → die nächste Absicht beim core hat `vehicles.tesla.mode == "pv"`, und das Änderungsprotokoll hat einen Eintrag mit Urheber „Standard-Lademodus“.
- [ ] **Step 6: Prüfkette** – `uv run pytest tests/unit/app tests/integration -q` → PASS; `uv run mypy --strict src`, `uv run lint-imports`.
- [ ] **Step 7: Commit** – `feat(app): Standard-Lademodus nach dem Abstecken setzen`

### Task 4: Auswahl „Nach dem Abstecken“ in den Einstellungen

**Files:**
- Modify: `ui/src/lib/api/schemas.ts` (~643: `wallbox_default_modes: v.optional(v.record(v.string(), v.picklist([...Modi])), {})`)
- Modify: `ui/src/lib/settings/model.ts` (Entwurf `wallboxDefaultModes: Record<string, ChargeMode | ''>`, Hin- und Rückweg ~397 und ~876, Fehlerpfad ~946 `wallbox_default_modes.<key>` → `wallboxDefaultModes.<key>`)
- Modify: `ui/src/lib/settings/SettingsEditor.svelte` (~252–270, Abschnitt „Ladestationen“)
- Test: `ui/src/lib/settings/model.test.ts`, `ui/src/lib/settings/SettingsEditor.test.ts`

**Interfaces:**
- Consumes: Feld aus Task 1; Modusnamen aus `ui/src/lib/charging/view.ts` (`MODES`, ohne `boost`) – falls dort nicht exportiert, eine exportierte Liste `CHARGE_MODE_LABELS` daraus machen statt sie zu kopieren.
- Produces: je Ladestation unter dem Namensfeld ein `<select>` mit Beschriftung „Nach dem Abstecken“, Optionen „Unverändert“ (`''`, wird nicht gespeichert) und die fünf Modi in der Reihenfolge PV-Überschuss, Min+PV, Sofort, Aus, Geplant.

- [ ] **Step 1: Tests schreiben** – model: `draft ohne Eintrag zeigt ''`, `'' wird beim Speichern weggelassen`, `'pv' wird gespeichert`, `Fehler an wallbox_default_modes.evcs landet am Feld`; Editor: `zeigt „Nach dem Abstecken“ je Ladestation mit „Unverändert“ vorgewählt`, `Auswahl „PV-Überschuss“ speichert {evcs: 'pv'}`.
- [ ] **Step 2: Tests laufen lassen, sie scheitern** – `cd ui && pnpm vitest run src/lib/settings` → FAIL.
- [ ] **Step 3: Umsetzen.**
- [ ] **Step 4: UI-Prüfkette** – `pnpm check && pnpm lint && pnpm test` → grün.
- [ ] **Step 5: Commit** – `feat(ui): Standard-Lademodus je Ladestation in den Einstellungen`

### Task 5: Doku

**Files:**
- Modify: `docs/superpowers/specs/2026-10-08-bindaems-design.md` (Absatz „Stand Plan Standard-Lademodus“ nach „Stand Plan Sofort-Limit“: Einstellung, Auslöser Abstecken, Ziel, Urheber, Nachholen)
- Modify: `docs/betrieb.md` (Abschnitt „Laden“: „Standard-Lademodus je Ladestation“; Abschnitt 4: „Rollback auf einen Stand vor dem Standard-Lademodus“ wie unter „Einordnung“)
- Modify: `docs/uebergabe.md` (Stand, „Offen an der Anlage“: nur `ems-app` neu, jederzeit)
- Test: `tests/unit/test_docs.py` (läuft mit, keine neuen Tests)

- [ ] **Step 1: Texte schreiben.**
- [ ] **Step 2: Doku-Tests** – `uv run pytest tests/unit/test_docs.py -q` → PASS.
- [ ] **Step 3: Commit** – `docs: Standard-Lademodus je Ladestation in Spec, Betrieb und Übergabe`
