# Phase 1c – Web-UI: Implementierungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ein deutschsprachiges, mobiltaugliches Web-UI, das ems-app ausliefert: Anmeldung (mit TOTP), Übersicht mit Energiefluss, Ladeständen, Preisen und PV-Prognose, Verlauf mit Tagesbilanz, Verbraucher, System, Einstellungen (überwiegend lesend), Konto sowie für Admins Benutzer und Änderungsprotokoll – live über WebSocket, hell und dunkel, als App installierbar, ohne jeden Schreibzugriff auf Geräte.

**Architecture:** SvelteKit als rein clientseitiges SPA (adapter-static, Fallback `index.html`) im Ordner `ui/`. ems-app liefert den Build aus (`app.ui_dir`, seit 1b mit SPA-Fallback und CSP-Hashes für Inline-Skripte); das Image der app baut das UI in einer Node-Stufe. Das UI spricht nur mit der eigenen API: REST mit Sitzungs-Cookie und CSRF-Header, Live-Werte über `WS /api/live`, alles andere als Abfragen im festen Takt. Antworten werden zur Laufzeit gegen Schemas geprüft; Vertragsdateien aus echten API-Antworten einer vorbelegten „Demo-Welt“ halten Backend und UI zusammen. Dieselbe Demo-Welt läuft als Demo-Backend für Playwright und die lokale Entwicklung.

**Tech Stack:** SvelteKit 2 mit Svelte 5 (Runes), TypeScript 6, Vite 8, ECharts 6, valibot, uqr (QR-Code); Vitest 5 mit jsdom und Testing Library, Playwright 1.56.1; ESLint 10, Prettier 3; pnpm 10 auf Node 22. Backend wie in 1b (Python 3.12, FastAPI, pytest, respx).

**Spec:** `docs/superpowers/specs/2026-10-08-bindaems-design.md`. Ausführende lesen Spec und Plan zusammen; Verweise wie „Spec 13“ beziehen sich darauf. Vorgänger: `docs/superpowers/plans/2026-10-09-phase-1b-app-backend.md` (Plan 1b, umgesetzt und gemergt; seine Global Constraints gelten für den Python-Teil weiter).

## Einordnung

| Plan | Inhalt | Stand |
|---|---|---|
| 1a | Fundament und Datenerfassung (ems-core, nur lesend) | fertig |
| 1b | ems-app-Backend: Login, Einstellungen, Verbraucher, Preise, Tarif v1, PV-Prognose, Abrechnung v1, HA-Sensoren, API | fertig |
| **1c** (dieser Plan) | Web-UI: Anmeldung, Übersicht, Verlauf, Verbraucher, System, Einstellungen, Konto, Benutzer, Protokoll | – |

Danach folgt die Abnahme der Phase 1 an der Anlage (Spec 18); Task 18 bereitet die Checkliste vor.

## Präzisierungen gegenüber der Spec

1. **SvelteKit 2, nicht 3.** SvelteKit 3.0 erschien am 01.10.2026; es verlegt die Konfiguration nach `vite.config`, ersetzt `$lib` durch `#lib` und entfernt `$app/stores`. SvelteKit 2.70 läuft bereits mit Svelte 5.57, Vite 8 und TypeScript 6. Dieser Plan nutzt nur, was es in 3.0 weiter gibt (`$app/state`, `$app/navigation`, Runes); der Umstieg ist ein eigener späterer Schritt mit dem offiziellen Migrationswerkzeug.
2. **TypeScript 6.0**, weil `svelte-check` 4.7 und `typescript-eslint` 8.71 TypeScript 7 noch nicht unterstützen.
3. **SPA:** adapter-static mit Fallback `index.html`, `ssr = false` und `prerender = false` im Wurzel-Layout; keine vorgerenderten Seiten. Der Server liefert seit 1b für jeden Nicht-API-Pfad `index.html` und erlaubt Inline-Skripte nur über ihre SHA-256-Hashes. Das deckt das Startskript von SvelteKit und das Theme-Skript in `app.html` ab. Die Hashes entstehen beim Start der app; ein Image-Wechsel startet sie ohnehin neu.
4. **PWA ohne Service Worker:** Manifest, Icons und `theme-color`. Installiert wird über das Browsermenü (Chrome verlangt dafür seit Version 108 mobil bzw. 112 am Desktop keinen Service Worker mehr; iOS: „Zum Home-Bildschirm“). Ein Service Worker brächte Cache-Fallen bei Updates und Sitzungen, aber keinen Nutzen: offline gibt es nichts zu bedienen (Spec 13).
5. **Leerlauf (Spec 14):** Nur Bedienung verlängert eine Sitzung. Automatische Abfragen des UI senden `X-Bindaems-Background: 1` und verlängern nicht (Task 3). Der Live-WebSocket verlängert schon seit 1b nicht und schließt mit 4401, sobald die Sitzung abgelaufen ist. Ein Admin, der die Übersicht 30 min nur ansieht, wird also abgemeldet.
6. **Seiten in 1c** (Spec 18, Phase 1): Anmeldung, Übersicht, Verlauf (einfache Diagramme und Tagesbilanz), Verbraucher, System, Einstellungen, Konto und für Admins Benutzer und Änderungsprotokoll. Zeitplan, Laden, Statistik, Entscheidungen und „Warum?“ folgen mit den Phasen 2–4, auf der System-Seite Watchdog, Prüfprotokoll-Status und der Dry-Run/Live-Schalter mit Phase 2. Die Rolle „Bedienen“ sieht in 1c dasselbe wie „Lesen“.
7. **Energiefluss:** ein Stern um den Hausanschluss. Jeder Zweig (PV, Netz, Akku, Haus, je Wallbox) zeigt Leistung und Richtung. Eine Live-Zuordnung „PV → Akku“ gibt es nicht; der core teilt Flüsse nur je Viertelstunde zu. Weitere Verbraucher und „Sonstiges“ zeigt die Verbraucherkarte daneben.
8. **Einstellungen überwiegend lesend:** Alle sehen Laufzeit-Einstellungen und harte Grenzen. Admins ändern im UI die Werte der festen Tarifbestandteile, die OeMAG-Monatswerte und die Preis-Einstellungen; alles andere (Zeitfenster, Gültigkeit, PV-Modell, neue Bestandteile) per YAML-Export und -Import. Nach einer Änderung der Tarifwerte bietet das UI die Neubewertung der Abrechnung an (`POST /api/ledger/reprice`).
9. **Anzeige:** Zeiten immer in Europe/Vienna, Zahlen im Format de-AT (Dezimalkomma, geschütztes Leerzeichen vor der Einheit). Fehlende Werte erscheinen als „–“, nie als 0. Messwerte mit Qualität `stale` sind als „veraltet“ markiert, `invalid` gilt als fehlend.
10. **Antworten werden geprüft:** valibot-Schemas beschreiben jede genutzte Antwort, die Typen leiten sich daraus ab. Passt eine Antwort nicht, zeigt das UI „Unerwartete Antwort vom Server“ statt falscher Werte.
11. **Vertragsdateien statt OpenAPI:** Ein Python-Test baut eine Demo-Welt (echte Dienste und Router, aufgezeichnete Preise und Prognose, simulierter core, InfluxDB-Ersatz) und vergleicht die Antworten aller genutzten Endpunkte mit JSON-Dateien in `ui/src/lib/api/contract/`. Vitest prüft dieselben Dateien gegen die Schemas. Ändert sich eine Antwort, schlägt einer der beiden Tests fehl; `UPDATE_UI_CONTRACT=1` schreibt die Dateien neu.
12. **Demo-Backend:** Die Demo-Welt läuft auch als Server (`tests/e2e/ui_server.py`) mit eingefrorener Uhr (09.10.2026, 10:00 Ortszeit), simulierten Live-Werten und InfluxDB-Ersatz. Er dient Playwright und der lokalen Entwicklung (`pnpm dev` leitet `/api` dorthin) und greift auf kein Gerät und kein Internet zu.
13. **Playwright 1.56.1, exakt:** passt zum vorinstallierten Chromium (Revision 1194) der Entwicklungsumgebung; die CI installiert denselben Browser.
14. **Kompression:** Die app komprimiert nicht. Die Doku empfiehlt `gzip` im Reverse Proxy für `/_app/` (Task 2).

## Global Constraints

- **Node und Paketverwaltung:** Node 22 (`"engines": {"node": ">=22.13"}`; die Entwicklungsumgebung hat 22.22.0), pnpm über `"packageManager": "pnpm@10.28.0"` in `ui/package.json`, Lockfile `ui/pnpm-lock.yaml`. In CI und Image nur `pnpm install --frozen-lockfile`.
- **Abhängigkeiten** (Untergrenzen in `ui/package.json`, das Lockfile hält die genaue Version):
  - Laufzeit: `svelte ^5.57.0`, `@sveltejs/kit ^2.70.0`, `echarts ^6.1.0`, `valibot ^1.5.0`, `uqr ^0.1.3`
  - Build: `@sveltejs/adapter-static ^3.0.10`, `@sveltejs/vite-plugin-svelte ^7.3.0`, `vite ^8.3.0`, `typescript ~6.0.3`, `svelte-check ^4.7.6`
  - Tests: `vitest ^5.0.3`, `jsdom ^29.1.1` (Version 30 verlangt Node ≥ 22.22.2), `@testing-library/svelte ^5.4.2`, `@testing-library/jest-dom ^7.0.1`, `@playwright/test 1.56.1` (exakt)
  - Lint und Format: `eslint ^10.12.0`, `@eslint/js ^10.0.1`, `typescript-eslint ^8.71.1`, `eslint-plugin-svelte ^3.23.1`, `globals ^17.13.0`, `prettier ^3.9.9`, `prettier-plugin-svelte ^4.1.1`
  - Weitere Abhängigkeiten nur mit Ruling.
- **TypeScript:** `strict`; kein `any` (ESLint), kein `@ts-ignore`. Svelte 5 mit Runes (`$state`, `$state.raw`, `$derived`, `$effect`, `$props`), keine Svelte-4-Stores. Aus SvelteKit nur `$app/state` und `$app/navigation`, nie `$app/stores`.
- **Sprache:** Bezeichner englisch, UI-Texte und Kommentare deutsch.
- **Zahlen und Zeiten:** nur über `ui/src/lib/format.ts` und `ui/src/lib/time.ts` (Europe/Vienna, de-AT). Zeitstempel vom Server nur über `parseIso` lesen (der Server liefert Mikrosekunden; Safari garantiert nur Millisekunden).
- **CSP (seit 1b):** `script-src 'self'` plus Hashes der Inline-Skripte in `index.html`, `style-src 'self' 'unsafe-inline'`, `img-src 'self' data:`, `connect-src 'self'`, `frame-ancestors 'none'`. Daraus folgt: kein `eval`/`new Function`, keine externen Ressourcen (Schriften, CDNs, Bilder), Inline-Skripte nur in `app.html`, Bilder nur aus `static/` oder als `data:`-URL (QR-Code).
- **API-Zugriffe:** nur über `ui/src/lib/api/`. Körper als JSON mit `Content-Type: application/json`; `X-CSRF-Token` aus dem Cookie `bindaems_csrf` bei POST, PUT, PATCH und DELETE; `X-Bindaems-Background: 1` bei automatischen Abfragen. Zeitparameter als UTC mit `Z`. Pfade exakt wie in der API (ohne abschließenden Schrägstrich).
- **Browser-Speicher:** nur die Theme-Wahl in `localStorage` (`bindaems.theme`); keine Daten, keine Tokens.
- **Rollen (Spec 14):** Admin-Seiten (Benutzer, Protokoll) und Admin-Aktionen erscheinen nur für Admins; die Prüfung bleibt beim Server. „Bedienen“ wie „Lesen“.
- **Mobil zuerst:** ab 360 px Breite kein waagrechtes Scrollen der Seite (breite Tabellen scrollen in ihrem eigenen Container), Bedienelemente mindestens 44 × 44 px, sichtbarer Tastaturfokus, `prefers-reduced-motion` schaltet Animationen ab.
- **Abfragetakt:** Live-Werte nur über `/api/live`. Abfragen: System 15 s, Verbraucher 15 s, Preis jetzt 60 s, Preise und Prognose 5 min, Verlauf „Heute“ 60 s; nie in verborgenen Tabs.
- **Keine Gerätezugriffe:** Das UI spricht nur mit ems-app; das Demo-Backend nur mit sich selbst.
- **Prüfbefehle:** UI `cd ui && pnpm lint && pnpm check && pnpm test && pnpm build`; Python mit dem bekannten Prüfskript (ruff format und check, mypy, lint-imports, pytest).

## Review Focus

1. **Admin lässt die Übersicht offen:** Nach 30 min ohne Bedienung endet die Sitzung, obwohl Abfragen und WebSocket weiterlaufen; das UI landet auf der Anmeldung mit „Sitzung abgelaufen“, statt still alte Werte zu zeigen. Tests: Task 3 `test_background_requests_do_not_keep_the_session_alive`, Task 6 „4401 meldet die abgelaufene Sitzung“, Task 7 „401 führt zur Anmeldung mit abgelaufen=1“.
2. **core oder app startet neu, das Netz bricht weg:** Die Übersicht zeigt binnen Sekunden „veraltet“ oder „getrennt“, verbindet von selbst neu und zeigt nie alte Werte als aktuell. Tests: Task 6 „markiert Daten nach 5 s ohne Nachricht als veraltet“ und „verbindet mit 1, 2, 5, 10, 30 s Abstand neu“, Task 9 „veraltete Daten grauen den Energiefluss aus“.
3. **Fehlende oder veraltete Messwerte:** `null` in `derived`, Qualität `stale` oder `invalid`, Unterverbraucher messen mehr als ihr Elternverbraucher – Anzeige „–“, „veraltet“ oder Hinweis, nie 0 W. Tests: Task 9 „unbekannte Werte zeigen einen Strich“ und „Ladestände tragen ihre Qualität“, Task 12 „Abweichung wird angezeigt“.
4. **Zeitumstellung:** 25-h- und 23-h-Tage in Zeiträumen, Tagesbilanz und Diagrammen. Tests: Task 1 „dauern beim Ende der Sommerzeit 25 h“, Task 11 „Heute am 25.10. umfasst 25 h“ und „Tagesbilanz zeigt 100 Viertelstunden“.
5. **Gleichzeitige Änderung der Einstellungen und Server-Validierung:** 409 (veraltete `base_version`) und 422 (Fehlerliste) werden verständlich gezeigt, Eingaben gehen nicht still verloren. Tests: Task 5 „übersetzt Fehlerlisten“, Task 14 „Konflikt behält die Eingaben und bietet Neuladen an“ und „Serverfehler erscheinen am Feld“.

## Dateistruktur

```
ui/
├── package.json, pnpm-lock.yaml, svelte.config.js, vite.config.ts, tsconfig.json
├── eslint.config.js, .prettierrc, .prettierignore, playwright.config.ts
├── scripts/render-icons.mjs        PNG-Icons aus static/icon.svg (einmalig, Ergebnis eingecheckt)
├── static/                          manifest.webmanifest, icon.svg, favicon.svg, icons/*.png
├── tests/e2e/                       Playwright gegen das Demo-Backend
└── src/
    ├── app.html, app.css, app.d.ts, test-setup.ts
    ├── routes/                      +layout.ts/.svelte, +error.svelte, +page.svelte (Übersicht),
    │                                login/, verlauf/, verbraucher/, system/, einstellungen/,
    │                                konto/, benutzer/, protokoll/
    └── lib/
        ├── format.ts, time.ts, navigation.ts, nav.ts, theme.svelte.ts, session.svelte.ts
        ├── live.svelte.ts, resource.svelte.ts
        ├── api/                     client.ts, errors.ts, schemas.ts, endpoints.ts, contract/*.json
        ├── components/              Card, Notice, StatusDot, Icon, Dialog
        ├── charts/                  echarts.ts, EChart.svelte, palette.ts, options.ts
        ├── dashboard/               flow.ts, EnergyFlow, socs.ts, SocList, notices.ts, Notices,
        │                            prices.ts, PriceNow, ConsumerSummary
        ├── history/                 ranges.ts, balance.ts, SeriesPicker, BalanceTable
        ├── consumers/               tree.ts, form.ts, ConsumerTree, ConsumerForm
        ├── system/                  view.ts, SignalTable
        ├── settings/                model.ts, TariffView, SettingsEditor, LimitsView,
        │                            ImportExport, RepriceForm
        ├── account/                 qr.ts, PasswordForm, TotpPanel
        ├── users/                   UserForm
        └── audit/                   view.ts
tests/ui_world.py                    Demo-Welt (Vertragsdateien, Demo-Backend)
tests/integration/test_ui_contract.py
tests/e2e/ui_server.py               Demo-Backend für Playwright und `pnpm dev`
```

Komponenten (`.svelte`) heißen in PascalCase, Module in Kleinbuchstaben. Tests liegen neben dem Modul (`*.test.ts`, Vitest); Playwright-Tests heißen `*.spec.ts` und liegen unter `ui/tests/e2e/`.

---
### Task 1: UI-Grundgerüst mit Format- und Zeithilfen

**Files:**
- Create: `ui/package.json`, `ui/pnpm-lock.yaml` (von pnpm erzeugt), `ui/svelte.config.js`, `ui/vite.config.ts`, `ui/tsconfig.json`, `ui/eslint.config.js`, `ui/.prettierrc`, `ui/.prettierignore`, `ui/src/app.html`, `ui/src/app.d.ts`, `ui/src/app.css`, `ui/src/test-setup.ts`, `ui/src/routes/+layout.ts`, `ui/src/routes/+page.svelte`, `ui/src/lib/format.ts`, `ui/src/lib/time.ts`
- Modify: `.gitignore` (`ui/test-results/`, `ui/playwright-report/`), `README.md` (Abschnitt „Web-UI entwickeln“)
- Test: `ui/src/lib/format.test.ts`, `ui/src/lib/time.test.ts`

**Interfaces:**
- Produces:
  - Skripte in `ui/package.json`: `dev` (`vite dev`), `build` (`vite build`), `preview`, `prepare` (`svelte-kit sync || echo ''`), `check` (`svelte-kit sync && svelte-check --tsconfig ./tsconfig.json --fail-on-warnings`), `lint` (`prettier --check . && eslint .`), `format` (`prettier --write .`), `test` (`vitest run`), `e2e` (`playwright test`)
  - `format.ts`: `DASH = '–'`, `NBSP = ' '`, `formatPower(w: number | null | undefined): string`, `formatEnergy(kwh)`, `formatCt(ct)`, `formatEur(eur)`, `formatSoc(pct)`, `formatRatio(ratio)` (alle mit derselben Signatur wie `formatPower`), `formatTime(iso: string): string`, `formatDateTime(iso: string): string`, `formatDay(date: string): string` (`date` als `YYYY-MM-DD`)
  - `time.ts`: `VIENNA = 'Europe/Vienna'`, `parseIso(iso: string): number | null` (Epoch-ms), `todayVienna(now?: Date): string`, `addDays(date: string, days: number): string`, `localDayRange(date: string): { from: string; to: string }`, `localRange(first: string, last: string): { from: string; to: string }` (vom Beginn des ersten bis zum Ende des letzten lokalen Tages; Grenzen als `toISOString()`)

- [ ] **Step 1: Projekt anlegen**

`ui/package.json` mit `"name": "bindaems-ui"`, `"private": true`, `"type": "module"`, `packageManager`, `engines`, den Skripten oben und den Abhängigkeiten aus den Global Constraints (Laufzeit unter `dependencies`, alles andere unter `devDependencies`); dann `cd ui && pnpm install`. Festlegungen für die übrigen Dateien:

- `svelte.config.js`: `adapter-static` mit `pages: 'build'`, `assets: 'build'`, `fallback: 'index.html'`, `precompress: false`, `strict: true`; Runes-Modus für alle Projektdateien erzwingen, nicht für `node_modules` (`compilerOptions.runes` wie in der Vorlage von `npx sv create`); `vitePreprocess()`.
- `vite.config.ts`: Plugins `sveltekit()` und – nur unter Vitest (`process.env.VITEST`) – `svelteTesting()` aus `@testing-library/svelte/vite`. Dev-Proxy: `/api` (mit `ws: true`) und `/health` auf `process.env.BINDAEMS_BACKEND ?? 'http://127.0.0.1:8099'` (Demo-Backend aus Task 4), immer `changeOrigin: false`, sonst schließt der Live-WebSocket mit 4403. Vitest: `environment: 'jsdom'`, `include: ['src/**/*.test.ts']`, `setupFiles: ['src/test-setup.ts']`.
- `src/test-setup.ts`: `import '@testing-library/jest-dom/vitest';`
- `tsconfig.json`: `extends: './.svelte-kit/tsconfig.json'`, `strict`, `resolveJsonModule`.
- `eslint.config.js` (Flat Config): `@eslint/js` recommended, `typescript-eslint` recommended, `eslint-plugin-svelte` recommended und `prettier`, `globals.browser` und `globals.node`, Parser für `.svelte` mit `typescript-eslint`; ignoriert `build/`, `.svelte-kit/`, `test-results/`, `playwright-report/`.
- `.prettierrc`: Tabs, einfache Anführungszeichen, `printWidth: 100`, `prettier-plugin-svelte`. `.prettierignore`: `build`, `.svelte-kit`, `pnpm-lock.yaml`, `src/lib/api/contract`, `static/icons`, `test-results`, `playwright-report`.
- `src/app.html`: `<html lang="de">`, `meta charset`, `meta viewport` mit `width=device-width, initial-scale=1, viewport-fit=cover`, `%sveltekit.head%`, `<body data-sveltekit-preload-data="hover">` mit `%sveltekit.body%`. Keine externen Ressourcen.
- `src/routes/+layout.ts`: `export const ssr = false; export const prerender = false;`
- `src/routes/+page.svelte`: vorläufig nur `<h1>BindaEMS</h1>` (Task 9 ersetzt sie).
- `src/app.css`: vorerst leer bis auf `box-sizing: border-box` (Task 8 füllt es).

- [ ] **Step 2: Failing Tests schreiben**

```ts
// ui/src/lib/format.test.ts
import { describe, expect, it } from 'vitest';
import {
	DASH,
	formatCt,
	formatDateTime,
	formatDay,
	formatEnergy,
	formatEur,
	formatPower,
	formatRatio,
	formatSoc,
	formatTime
} from './format';

const NB = ' ';

describe('Leistung', () => {
	it('zeigt unter 1 kW Watt, darüber kW', () => {
		expect(formatPower(512)).toBe(`512${NB}W`);
		expect(formatPower(-200.4)).toBe(`-200${NB}W`);
		expect(formatPower(1234)).toBe(`1,23${NB}kW`);
		expect(formatPower(12345)).toBe(`12,3${NB}kW`);
	});

	it('zeigt fehlende Werte als Strich, nie als 0', () => {
		expect(formatPower(null)).toBe(DASH);
		expect(formatPower(undefined)).toBe(DASH);
		expect(formatPower(Number.NaN)).toBe(DASH);
	});
});

it('formatiert Energie, Preise, Geld und Anteile deutsch', () => {
	expect(formatEnergy(12.345)).toBe(`12,3${NB}kWh`);
	expect(formatEnergy(1.234)).toBe(`1,23${NB}kWh`);
	expect(formatCt(13.444)).toBe(`13,44${NB}ct/kWh`);
	expect(formatEur(1.5)).toBe(`€${NB}1,50`);
	expect(formatSoc(55.4)).toBe(`55${NB}%`);
	expect(formatRatio(0.873)).toBe(`87${NB}%`);
	expect(formatEur(null)).toBe(DASH);
});

it('zeigt Zeiten in Wien', () => {
	expect(formatTime('2026-10-09T08:00:00+00:00')).toBe('10:00');
	expect(formatDateTime('2026-10-09T08:00:00.123456+00:00')).toBe('09.10.2026, 10:00');
	expect(formatDay('2026-10-09')).toBe('Fr., 09.10.');
});
```

```ts
// ui/src/lib/time.test.ts
import { describe, expect, it } from 'vitest';
import { addDays, localDayRange, localRange, parseIso, todayVienna } from './time';

describe('lokale Tage', () => {
	it('dauern an normalen Tagen 24 h', () => {
		expect(localDayRange('2026-10-09')).toEqual({
			from: '2026-10-08T22:00:00.000Z',
			to: '2026-10-09T22:00:00.000Z'
		});
	});

	it('dauern beim Ende der Sommerzeit 25 h', () => {
		expect(localDayRange('2026-10-25')).toEqual({
			from: '2026-10-24T22:00:00.000Z',
			to: '2026-10-25T23:00:00.000Z'
		});
	});

	it('dauern beim Beginn der Sommerzeit 23 h', () => {
		expect(localDayRange('2026-03-29')).toEqual({
			from: '2026-03-28T23:00:00.000Z',
			to: '2026-03-29T22:00:00.000Z'
		});
	});

	it('reichen über mehrere Tage', () => {
		expect(localRange('2026-10-24', '2026-10-25')).toEqual({
			from: '2026-10-23T22:00:00.000Z',
			to: '2026-10-25T23:00:00.000Z'
		});
	});
});

it('bestimmt „heute“ in Wien, nicht in UTC', () => {
	expect(todayVienna(new Date('2026-10-09T22:30:00Z'))).toBe('2026-10-10');
	expect(addDays('2026-10-31', 1)).toBe('2026-11-01');
	expect(addDays('2026-03-01', -1)).toBe('2026-02-28');
});

it('liest Zeitstempel mit Mikrosekunden (Safari kennt nur Millisekunden)', () => {
	expect(parseIso('2026-10-09T08:00:00.123456+00:00')).toBe(Date.UTC(2026, 9, 9, 8, 0, 0, 123));
	expect(parseIso('2026-10-09T08:00:00+00:00')).toBe(Date.UTC(2026, 9, 9, 8));
	expect(parseIso('kein Datum')).toBeNull();
});
```

- [ ] **Step 3: Tests laufen lassen**

Run: `cd ui && pnpm vitest run src/lib`
Expected: FAIL – `./format` und `./time` lassen sich nicht auflösen.

- [ ] **Step 4: `format.ts` und `time.ts` implementieren**

`Intl.NumberFormat('de-AT')` und `Intl.DateTimeFormat('de-AT', { timeZone: 'Europe/Vienna' })`, Einheit immer mit `NBSP` angehängt. Leistung: gerundet unter 1000 W in W ohne Nachkommastellen, sonst kW mit 2 (unter 10 kW) bzw. 1 Nachkommastelle; Energie analog in kWh (unter 10 mit 2, sonst 1 Nachkommastelle); ct mit 2; SOC und Anteil ohne Nachkommastellen; `formatEur` mit `style: 'currency'`. `null`, `undefined` und nicht endliche Zahlen ergeben `DASH`. `parseIso` kürzt Sekundenbruchteile per Regex auf drei Stellen, bevor es `Date.parse` aufruft, und liefert bei `NaN` `null`. Lokale Mitternacht: UTC-Versatz von Europe/Vienna zum Zeitpunkt über `Intl.DateTimeFormat(..., { timeZoneName: 'longOffset' })` bestimmen und einmal nachkorrigieren (zwei Durchläufe genügen für die Umstellungstage).

- [ ] **Step 5: Tests und Prüfungen laufen lassen**

Run: `cd ui && pnpm vitest run src/lib && pnpm lint && pnpm check && pnpm build && grep -c '<script' build/index.html`
Expected: alle Tests PASS, Lint und Check ohne Befund, `build/index.html` existiert und enthält genau ein Inline-Skript (das Startskript von SvelteKit), Ausgabe `1`.

- [ ] **Step 6: README ergänzen und committen**

`README.md`, neuer Abschnitt „Web-UI entwickeln“: `cd ui && pnpm install`, `pnpm dev` (Proxy auf das Demo-Backend aus Task 4 oder `BINDAEMS_BACKEND=http://<host>:8080`), `pnpm test`, `pnpm check`, `pnpm lint`, `pnpm build`.

```bash
git add .gitignore README.md ui
git commit -m "feat(ui): SvelteKit-Gerüst mit Format- und Zeithilfen"
```

---

### Task 2: UI im Image der app und in der CI

**Files:**
- Modify: `src/bindaems/shared/config.py:246` (`ui_dir: Path | None = Path("/app/ui")`), `tests/unit/shared/test_config.py:93`, `deploy/Dockerfile.app`, `.dockerignore`, `.github/workflows/ci.yml`, `tests/unit/test_deploy_files.py`, `docs/betrieb.md` (Abschnitt 6, gzip-Hinweis)

**Interfaces:**
- Consumes: `ui/package.json`, `ui/pnpm-lock.yaml` und das Skript `build` aus Task 1.
- Produces: das UI liegt im Image unter `/app/ui`, Standard von `app.ui_dir`; CI-Job `ui`; der Job `docker` braucht `python` und `ui`.

- [ ] **Step 1: Failing Tests schreiben**

```python
# tests/unit/test_deploy_files.py
def test_app_image_builds_and_ships_the_ui() -> None:
    text = Path("deploy/Dockerfile.app").read_text()
    assert "FROM node:22-bookworm-slim AS ui" in text
    assert "pnpm install --frozen-lockfile" in text and "pnpm build" in text
    assert "COPY --from=ui /ui/build /app/ui" in text


def test_docker_context_contains_ui_sources_but_no_build_output() -> None:
    lines = Path(".dockerignore").read_text().splitlines()
    assert "ui" not in lines
    assert {"ui/node_modules", "ui/build", "ui/.svelte-kit"} <= set(lines)


def test_ci_checks_and_builds_the_ui() -> None:
    ci = yaml.safe_load(Path(".github/workflows/ci.yml").read_text())
    runs = " ".join(step.get("run", "") for step in ci["jobs"]["ui"]["steps"])
    for command in (
        "pnpm install --frozen-lockfile",
        "pnpm lint",
        "pnpm check",
        "pnpm test",
        "pnpm build",
    ):
        assert command in runs
    assert set(ci["jobs"]["docker"]["needs"]) == {"python", "ui"}
```

In `tests/unit/shared/test_config.py::test_app_and_ha_mqtt_from_example` wird `cfg.app.ui_dir is None` zu `cfg.app.ui_dir == Path("/app/ui")`.

- [ ] **Step 2: Tests laufen lassen**

Run: `uv run pytest tests/unit/test_deploy_files.py tests/unit/shared/test_config.py -q`
Expected: FAIL in den drei neuen Tests und in `test_app_and_ha_mqtt_from_example`.

- [ ] **Step 3: Image, Kontext, CI und Standardpfad umsetzen**

- `deploy/Dockerfile.app`: erste Stufe `FROM node:22-bookworm-slim AS ui`, `WORKDIR /ui`, `ENV COREPACK_ENABLE_DOWNLOAD_PROMPT=0`, `RUN corepack enable`, zuerst `COPY ui/package.json ui/pnpm-lock.yaml ./` und `RUN pnpm install --frozen-lockfile` (eigene Schicht), dann `COPY ui ./` und `RUN pnpm build`. In der Endstufe vor `useradd`: `COPY --from=ui /ui/build /app/ui`. Kopfkommentar auf „Phase 1c: mit Web-UI“ anpassen. Alles, was `test_app_dockerfile_runs_as_non_root_with_healthcheck` prüft, bleibt.
- `.dockerignore`: Zeile `ui` ersetzen durch `ui/node_modules`, `ui/build`, `ui/.svelte-kit`, `ui/test-results`, `ui/playwright-report`, `ui/tests`.
- `.github/workflows/ci.yml`: neuer Job `ui` (`ubuntu-latest`, `defaults.run.working-directory: ui`): `actions/checkout@v4`; `pnpm/action-setup@v4` mit `package_json_file: ui/package.json`; `actions/setup-node@v4` mit `node-version: "22"`, `cache: pnpm`, `cache-dependency-path: ui/pnpm-lock.yaml`; Schritte `pnpm install --frozen-lockfile`, `pnpm lint`, `pnpm check`, `pnpm test`, `pnpm build` mit deutschen Namen wie im Job `python`. Job `docker`: `needs: [python, ui]`.
- `src/bindaems/shared/config.py`: Standard `Path("/app/ui")`. Fehlt dort `index.html` (Entwicklung ohne Build), protokolliert `_mount_ui` wie bisher eine Warnung und liefert nur die API.
- `docs/betrieb.md` Abschnitt 6: das UI kommt mit dem Image; nginx-Beispiel um `gzip on; gzip_types text/css application/javascript image/svg+xml;` im `location /`-Block ergänzen (nur statische Dateien profitieren; die API antwortet klein).

- [ ] **Step 4: Tests und Prüfungen laufen lassen**

Run: Prüfskript (ruff format und check, mypy, lint-imports, pytest)
Expected: alle Tests PASS. Die Compose-Datei bleibt unverändert. Das Image selbst baut erst die CI (hier kein Docker-Daemon); die Befehle der Node-Stufe wurden in Task 1 lokal ausgeführt.

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/shared/config.py tests/unit deploy/Dockerfile.app .dockerignore .github/workflows/ci.yml docs/betrieb.md
git commit -m "build: Web-UI im Image der app, UI-Job in der CI"
```

---

### Task 3: Automatische Abfragen verlängern keine Sitzung

**Files:**
- Modify: `src/bindaems/app/auth/web.py`
- Test: `tests/unit/app/auth/test_web.py`

**Interfaces:**
- Consumes: `AuthService.resolve(token, *, touch: bool = True)` aus 1b.
- Produces: `BACKGROUND_HEADER = "X-Bindaems-Background"` in `bindaems.app.auth.web`; mit dem Wert `"1"` prüft `Guard.require` die Sitzung mit `touch=False`. Das UI (Task 5) sendet diesen Header bei jeder automatischen Abfrage.

- [ ] **Step 1: Failing Tests schreiben**

```python
# tests/unit/app/auth/test_web.py
BACKGROUND = {"X-Bindaems-Background": "1"}


def test_background_requests_do_not_keep_the_session_alive(make_client, auth, clock) -> None:
    # Übersicht offen, niemand bedient: die Admin-Sitzung endet nach 30 min (Spec 14)
    client = make_client()
    login_as(client, auth, "admin")
    clock.advance(20 * 60)
    assert client.get("/api/auth/me", headers=BACKGROUND).status_code == 200
    clock.advance(11 * 60)
    assert client.get("/api/auth/me", headers=BACKGROUND).status_code == 401


def test_user_requests_keep_the_session_alive(make_client, auth, clock) -> None:
    client = make_client()
    login_as(client, auth, "admin")
    clock.advance(20 * 60)
    assert client.get("/api/auth/me").status_code == 200
    clock.advance(11 * 60)
    assert client.get("/api/auth/me").status_code == 200
```

- [ ] **Step 2: Tests laufen lassen**

Run: `uv run pytest tests/unit/app/auth/test_web.py -q -k keep_the_session_alive`
Expected: FAIL in `test_background_requests_do_not_keep_the_session_alive` (200 statt 401); der zweite Test besteht bereits und bleibt als Gegenprobe.

- [ ] **Step 3: `Guard.require` anpassen**

`touch = request.headers.get(BACKGROUND_HEADER) != "1"` und `self._auth.resolve(token, touch=touch)`; Kommentar: Abfragen im Takt sind keine Bedienung.

- [ ] **Step 4: Tests laufen lassen**

Run: Prüfskript
Expected: alles grün.

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/app/auth/web.py tests/unit/app/auth/test_web.py
git commit -m "feat(app): automatische Abfragen verlängern keine Sitzung"
```

---

### Task 4: Demo-Welt, Vertragsdateien und Demo-Backend

**Files:**
- Create: `tests/ui_world.py`, `tests/integration/test_ui_contract.py`, `tests/e2e/ui_server.py`, `ui/src/lib/api/contract/*.json` (vom Test erzeugt)
- Modify: `README.md` (Demo-Backend)

**Interfaces:**
- Consumes: `AppRuntime(cfg, secrets, *, clock, serve=False)` und seine Attribute (`app`, `auth`, `settings`, `consumers`, `pipeline`, `forecast`, `ledger`, `ha_values`, `live`, `engine`, `_sink`); `CoreRuntime(cfg, secrets, *, clock, adapters, sink, serve_api=False, timer)` mit `store.register_source`, `store.set_connected`, `store.update`, `cycle_once()`, `state()`, `health()`; `LiveState` (Felder `connected`, `state`, `alarms`, `health`, `updated_at`, Methode `publish`); `LedgerService.handle_stream_message(data)` (async, `data` wie `core.runtime._slot_json`); `HaValueCache.refresh(refs)`; aus `tests/app_helpers.py`: `T_APP`, `mock_all_sources`, `set_grid_usage`, `set_feed_in`; `OPEN_METEO_URL` und die Aufnahme `tests/fixtures/openmeteo_sued_2026-10-09.json`; `FakeSink` aus `tests/helpers.py`.
- Produces (`tests/ui_world.py`):
  - `T_DEMO = T_APP` (09.10.2026, 10:00 Ortszeit), `DEMO_PASSWORD = "demo-passwort-1"`, `DEMO_TOTP_SECRET = "JBSWY3DPEHPK3PXP"`
  - Benutzer: `admin` (Admin, ohne TOTP), `sicher` (Admin, TOTP aktiv mit `DEMO_TOTP_SECRET`), `gast` (Lesen); alle mit `DEMO_PASSWORD`
  - `class World` mit `app: AppRuntime`, `core: CoreRuntime`, `step_live(tick: int = 0) -> None` (neue Messwerte für Takt `tick`, ein core-Zyklus, `state()` und `health()` in `app.live`, Nachricht `{"type": "state", "data": …}` über `app.live.publish`) und `close() -> None`
  - `async def build_world(cfg: Config, data_dir: Path, *, ui_dir: Path | None = None) -> World` – immer mit `ManualClock(T_DEMO)`
  - Vertragsdateien `ui/src/lib/api/contract/<name>.json` für: `me`, `state`, `system`, `settings`, `settings-versions`, `limits`, `consumers`, `consumer-candidates`, `history-catalog`, `history`, `prices`, `prices-now`, `forecast`, `ledger-days`, `users`, `audit`, `live-hello`, `live-state`, `error-422`
  - `tests/e2e/ui_server.py`, gestartet als Modul aus dem Repository-Wurzelverzeichnis (sonst fehlt `tests` im Importpfad): `uv run python -m tests.e2e.ui_server [--host 127.0.0.1] [--port 8099] [--ui-dir ui/build] [--data-dir <neues Temp-Verzeichnis>]`

Die Demo-Welt im Detail:

1. **Konfiguration:** `deploy/config.example.yaml` mit `app.data_dir`/`backup_dir` im übergebenen Verzeichnis, `app.ui_dir` wie übergeben (oder `None`), `cookie_secure: false`, `core_url: http://127.0.0.1:9` (nie benutzt). Secrets: nur `internal_token`.
2. **Netz:** Ein `respx`-Router (`assert_all_mocked=True`) ist von `build_world` bis `close()` aktiv. Er beantwortet smartENERGY, Energy-Charts und aWATTar mit den Aufnahmen (`mock_all_sources`), Open-Meteo mit `openmeteo_sued_2026-10-09.json` und InfluxDB-`/query` mit einem Ersatz:
   - `SELECT mean(...)`: eine Reihe `v` mit Punkten alle `max(step, 900)` s zwischen den Grenzen `time >= …ms` und `time < …ms` der Abfrage. Werte: deterministische Tageskurve je Messgröße (Leistung W, SOC %, Preis ct/kWh). Der dritte Punkt fehlt absichtlich, damit Lücken vorkommen.
   - `SELECT last(...)` (HA-Werte): je angefragter `entity_id` ein fester Wert.
   - `SHOW SERIES`: zwei HA-Leistungsentitäten, `sensor.kueche_power` (W) und `sensor.waschmaschine_power` (W).
   - Jeder andere Netzzugriff ist ein Fehler.
3. **core:** `CoreRuntime` mit einem Ersatz-Adapter `victron`, der verbunden meldet, und konstantem `timer` (stabile `cycle_ms_p95`). Mittagssituation: PV ≈ 3,2 kW, Netzbezug ≈ 0,4 kW, Akku lädt ≈ 1 kW, Wall Connector lädt 1,38 kW, EVCS 0 W, `load.obergeschoss.power_w` 600 W, `load.buero.power_w` 150 W; Akku-SOC 55 % (`ok`), Tesla 70 % (Qualität `stale`), e-Golf ohne Wert. `step_live(tick)` verändert PV und Haus um wenige Prozent je Takt. Die Werte müssen über die echten Ableitungen des core (`derive`) diese Größen ergeben.
4. **app:** `AppRuntime(..., clock=ManualClock(T_DEMO), serve=False)`; `app._sink` an die laufende Schleife binden (wie `AppRuntime.run`). Danach:
   - Benutzer anlegen; für `sicher` das TOTP-Geheimnis per SQL setzen und aktivieren.
   - Einstellungen: Netznutzungsentgelt 8,11 ct und OeMAG `{"2026-09": 7.3}` (zwei Versionen; Netzverlustentgelt, Elektrizitätsabgabe und Erneuerbaren-Förderbeitrag bleiben leer, damit Warnungen entstehen).
   - Verbraucher: „Obergeschoss“ (core `load.obergeschoss.power_w`) mit Kind „Büro“ (core `load.buero.power_w`), dazu „Küche“ (HA `sensor.kueche_power`, W); danach einmal `ha_values.refresh`.
   - `await pipeline.refresh()`, `await forecast.refresh()`, `step_live()`.
   - Abrechnung: vier `slot_flows` für 09:00–10:00 Ortszeit über `ledger.handle_stream_message`.

Der Vertragstest meldet sich per `TestClient(app, base_url="https://testserver")` als `admin` an. `history` fragt `?series=grid,soc.battery&from=2026-10-09T06:00:00Z&to=2026-10-09T08:00:00Z` ab, `ledger-days` `?from=2026-10-08&to=2026-10-09`. `live-hello` ist die erste Nachricht von `WS /api/live`. `live-state` ist `{"type": "state", "data": app.live.state}`. `error-422` ist die Antwort auf `PUT /api/settings` mit `"value_ct": "abc"` beim Netznutzungsentgelt.

- [ ] **Step 1: Failing Tests schreiben**

```python
# tests/integration/test_ui_contract.py
"""Antworten der API in den Formen, die das UI erwartet (ui/src/lib/api/contract/).

Neu schreiben: UPDATE_UI_CONTRACT=1 uv run pytest tests/integration/test_ui_contract.py
"""

CONTRACT = Path("ui/src/lib/api/contract")
GET_ENDPOINTS = {
    "me": "/api/auth/me",
    "state": "/api/state",
    "system": "/api/system",
    "settings": "/api/settings",
    "settings-versions": "/api/settings/versions",
    "limits": "/api/limits",
    "consumers": "/api/consumers",
    "consumer-candidates": "/api/consumers/candidates",
    "history-catalog": "/api/history/catalog",
    "history": "/api/history?series=grid,soc.battery&from=2026-10-09T06:00:00Z"
    "&to=2026-10-09T08:00:00Z",
    "prices": "/api/prices",
    "prices-now": "/api/prices/now",
    "forecast": "/api/forecast/pv",
    "ledger-days": "/api/ledger/days?from=2026-10-08&to=2026-10-09",
    "users": "/api/users",
    "audit": "/api/audit",
}
OTHER = {"live-hello", "live-state", "error-422"}


async def test_api_responses_match_ui_contract(cfg, tmp_path) -> None:
    world = await build_world(cfg, tmp_path)
    try:
        actual = await asyncio.to_thread(collect_responses, world)  # GET, WebSocket, 422
    finally:
        world.close()
    mismatched = [name for name, body in actual.items() if not matches_contract(name, body)]
    assert sorted(actual) == sorted(GET_ENDPOINTS.keys() | OTHER)
    assert mismatched == []


async def test_demo_world_is_deterministic(cfg, tmp_path) -> None:
    # Sonst wechselten die Vertragsdateien bei jedem Lauf
    bodies = []
    for name in ("a", "b"):
        world = await build_world(cfg, tmp_path / name)
        try:
            bodies.append(await asyncio.to_thread(collect_responses, world))
        finally:
            world.close()
    assert bodies[0] == bodies[1]


def test_no_stale_contract_files() -> None:
    names = {path.stem for path in CONTRACT.glob("*.json")}
    assert names == GET_ENDPOINTS.keys() | OTHER
```

Hilfsfunktionen im Testmodul:
- `collect_responses(world: World) -> dict[str, object]`: meldet sich per `TestClient(world.app.app, base_url="https://testserver")` als `admin` an und sammelt die Antworten aller `GET_ENDPOINTS` (Status 200 verlangt), die erste WebSocket-Nachricht (`live-hello`), `live-state` und die 422-Antwort (`error-422`).
- `matches_contract(name: str, body: object) -> bool`: schreibt bei `UPDATE_UI_CONTRACT=1` die Datei (`json.dumps(body, indent=2, ensure_ascii=False, sort_keys=True) + "\n"`) und gibt `True` zurück; sonst vergleicht es mit dem gelesenen JSON.

- [ ] **Step 2: Tests laufen lassen**

Run: `uv run pytest tests/integration/test_ui_contract.py -q`
Expected: FAIL – `tests.ui_world` fehlt.

- [ ] **Step 3: `tests/ui_world.py` implementieren**

Wie unter „Die Demo-Welt im Detail“. Die Demo-Welt ist Testwerkzeug: Zugriffe auf private Attribute (`_sink`) und direktes SQL sind erlaubt, Änderungen an `src/` für sie nicht.

- [ ] **Step 4: Vertragsdateien erzeugen und prüfen**

Run: `UPDATE_UI_CONTRACT=1 uv run pytest tests/integration/test_ui_contract.py -q && uv run pytest tests/integration/test_ui_contract.py -q`
Expected: beide Läufe PASS. Die Dateien durchsehen: Mittagssituation im `live-state`, Warnungen zu fehlenden Tarifwerten in `system`, `other_w` und kein `mismatch` in `consumers`, Lücke in `history`, `origin: "primary"` in `prices`.

- [ ] **Step 5: Demo-Backend schreiben und starten**

`tests/e2e/ui_server.py`: Argumente wie oben, `build_world`, eine Aufgabe ruft jede Sekunde `world.step_live(tick)`, dann `uvicorn.Server(uvicorn.Config(world.app.app, host, port, log_level="warning", lifespan="off")).serve()`. Beim Start eine Zeile: Adresse, Benutzer `admin`, `sicher` und `gast`, Passwort `demo-passwort-1`. Datenverzeichnis ohne Angabe: neues Temp-Verzeichnis, also bei jedem Start ein frischer Stand.

Run: `uv run python -m tests.e2e.ui_server --port 8099 &` und dann `curl -s http://127.0.0.1:8099/health`, danach den Server beenden.
Expected: `{"status":"ok",…}`.

- [ ] **Step 6: Prüfen und committen**

`README.md`: Abschnitt „Demo-Backend“ (Start, Benutzer, eingefrorene Uhr, kein Gerät und kein Internet).

Run: Prüfskript
Expected: alles grün.

```bash
git add tests/ui_world.py tests/integration/test_ui_contract.py tests/e2e/ui_server.py ui/src/lib/api/contract README.md
git commit -m "test: Demo-Welt, Vertragsdateien für das UI und Demo-Backend"
```

---

### Task 5: API-Client, Fehlertexte und Schemas

**Files:**
- Create: `ui/src/lib/api/errors.ts`, `ui/src/lib/api/client.ts`, `ui/src/lib/api/schemas.ts`, `ui/src/lib/api/endpoints.ts`
- Test: `ui/src/lib/api/errors.test.ts`, `ui/src/lib/api/client.test.ts`, `ui/src/lib/api/contract.test.ts`

**Interfaces:**
- Consumes: Vertragsdateien aus Task 4; Header `X-Bindaems-Background` aus Task 3; Antwortformen aus Anhang A.
- Produces:
  - `errors.ts`: `NETWORK_ERROR = 'Keine Verbindung zum Server'`, `CONTRACT_ERROR = 'Unerwartete Antwort vom Server'`, `class ApiError extends Error` mit `readonly status: number` (0 = keine Verbindung), `readonly detail: string`, `readonly body: unknown`, `readonly retryAfterS: number | null`; `detailText(detail: unknown): string`; `fieldErrors(detail: unknown, prefix?: readonly (string | number)[]): Record<string, string>` (nur Einträge, deren `loc` mit `prefix` beginnt; Schlüssel = restlicher Pfad mit Punkten)
  - `client.ts`: `BACKGROUND_HEADER = 'X-Bindaems-Background'`, `type Method = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'`, `interface RequestOptions<T> { schema?: v.GenericSchema<unknown, T>; body?: unknown; background?: boolean; signal?: AbortSignal; redirectOn401?: boolean }`, `request<T = void>(method: Method, path: string, options?: RequestOptions<T>): Promise<T>`, `csrfToken(cookies?: string): string | null`, `onUnauthorized(handler: (() => void) | null): void`
  - `schemas.ts`: je Antwortform aus Anhang A ein Schema `<Name>Schema` und ein Typ `<Name> = v.InferOutput<typeof <Name>Schema>`: `Role`, `User`, `MeResponse`, `ComponentStatus`, `Alarm`, `Reading`, `Derived`, `CoreState`, `CoreHealth`, `StateResponse`, `SystemResponse`, `LiveMessage`, `TimeWindow`, `PriceComponent`, `RuntimeSettings`, `PriceSettings`, `SettingsMeta`, `SettingsCurrent`, `Limits`, `Consumer`, `TreeNode`, `ConsumersResponse`, `Candidates`, `CatalogEntry`, `HistoryResponse`, `PriceSlot`, `PriceStatus`, `PricesResponse`, `PricesNow`, `ForecastSlot`, `Forecast`, `DaySummary`, `AuditEntry`, `TotpSetup`, `Health`, `ValidationError`. Anfragekörper sind schlichte Interfaces: `LoginBody { username; password; totp?: string | null; remember?: boolean }`, `NewUser { username; password; role }`, `UserPatch { role?: Role; password?: string }`, `ConsumerInput` (Anhang A)
  - `endpoints.ts`: `interface Load { background?: boolean; signal?: AbortSignal }` und
    ```ts
    export const api: {
    	me(o?: Load & { redirectOn401?: boolean }): Promise<MeResponse>;
    	login(body: LoginBody): Promise<MeResponse>; // redirectOn401: false
    	logout(): Promise<void>;
    	changePassword(oldPassword: string, newPassword: string): Promise<void>;
    	totpSetup(password: string): Promise<TotpSetup>;
    	totpEnable(code: string): Promise<void>;
    	totpDisable(password: string): Promise<void>;
    	users(o?: Load): Promise<User[]>;
    	createUser(body: NewUser): Promise<User>;
    	updateUser(id: number, body: UserPatch): Promise<User>;
    	deleteUser(id: number): Promise<void>;
    	audit(limit: number, o?: Load): Promise<AuditEntry[]>;
    	health(o?: Load): Promise<Health>;
    	system(o?: Load): Promise<SystemResponse>;
    	settings(o?: Load): Promise<SettingsCurrent>;
    	settingsVersions(o?: Load): Promise<SettingsMeta[]>;
    	saveSettings(baseVersion: number, settings: RuntimeSettings, comment: string | null): Promise<SettingsCurrent>;
    	importSettings(yaml: string): Promise<SettingsCurrent>;
    	limits(o?: Load): Promise<Limits>;
    	consumers(o?: Load): Promise<ConsumersResponse>;
    	consumerCandidates(o?: Load): Promise<Candidates>;
    	createConsumer(input: ConsumerInput): Promise<Consumer>;
    	updateConsumer(id: number, input: ConsumerInput): Promise<Consumer>;
    	deleteConsumer(id: number): Promise<void>;
    	historyCatalog(o?: Load): Promise<CatalogEntry[]>;
    	history(series: string[], range: { from: string; to: string }, o?: Load): Promise<HistoryResponse>;
    	prices(range: { from: string; to: string } | null, o?: Load): Promise<PricesResponse>;
    	pricesNow(o?: Load): Promise<PricesNow>;
    	refreshPrices(): Promise<PriceStatus>;
    	forecast(o?: Load): Promise<Forecast>;
    	ledgerDays(first: string, last: string, o?: Load): Promise<DaySummary[]>;
    	reprice(first: string, last: string): Promise<{ repriced: number }>;
    };
    export const EXPORT_SETTINGS_URL = '/api/settings/export';
    ```

- [ ] **Step 1: Failing Tests schreiben**

```ts
// ui/src/lib/api/errors.test.ts
import { describe, expect, it } from 'vitest';
import error422 from './contract/error-422.json';
import { detailText, fieldErrors } from './errors';

describe('Fehlertexte', () => {
	it('übernimmt Texte des Servers', () => {
		expect(detailText('Benutzername oder Passwort falsch')).toBe(
			'Benutzername oder Passwort falsch'
		);
	});

	it('übersetzt Fehlerlisten', () => {
		const detail = [
			{
				type: 'float_parsing',
				loc: ['body', 'settings', 'tariff', 'components', 2, 'value_ct'],
				msg: 'Input should be a valid number, unable to parse string as a number',
				input: 'abc'
			},
			{ type: 'value_error', loc: ['body', 'name'], msg: 'Value error, Name fehlt', input: '' },
			{ type: 'missing', loc: ['body', 'role'], msg: 'Field required', input: {} }
		];
		expect(detailText(detail)).toBe(
			'settings.tariff.components.2.value_ct: Zahl erwartet\nname: Name fehlt\nrole: Angabe fehlt'
		);
		expect(fieldErrors(detail, ['body', 'settings'])).toEqual({
			'tariff.components.2.value_ct': 'Zahl erwartet'
		});
	});

	it('versteht die echte 422-Antwort', () => {
		expect(detailText(error422.detail)).toContain('Zahl erwartet');
	});

	it('fällt bei Unbekanntem auf einen allgemeinen Text zurück', () => {
		expect(detailText(undefined)).toBe('Unbekannter Fehler');
	});
});
```

```ts
// ui/src/lib/api/client.test.ts
import * as v from 'valibot';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { onUnauthorized, request } from './client';
import { ApiError, CONTRACT_ERROR, NETWORK_ERROR } from './errors';

const fetchMock = vi.fn();

function reply(status: number, body?: unknown, headers: Record<string, string> = {}) {
	fetchMock.mockResolvedValueOnce(
		new Response(body === undefined ? null : JSON.stringify(body), {
			status,
			headers: { 'Content-Type': 'application/json', ...headers }
		})
	);
}

function sentHeaders(call = 0): Headers {
	return new Headers(fetchMock.mock.calls[call][1].headers);
}

beforeEach(() => {
	vi.stubGlobal('fetch', fetchMock);
	document.cookie = 'bindaems_csrf=tok-123';
});

afterEach(() => {
	vi.unstubAllGlobals();
	fetchMock.mockReset();
	onUnauthorized(null);
});

it('sendet CSRF-Token und JSON bei schreibenden Anfragen', async () => {
	reply(204);
	await expect(request('PUT', '/api/settings', { body: { a: 1 } })).resolves.toBeUndefined();
	const [url, init] = fetchMock.mock.calls[0];
	expect([url, init.method, init.body]).toEqual(['/api/settings', 'PUT', '{"a":1}']);
	expect(sentHeaders().get('X-CSRF-Token')).toBe('tok-123');
	expect(sentHeaders().get('Content-Type')).toBe('application/json');
});

it('kennzeichnet automatische Abfragen und liest ohne CSRF-Token', async () => {
	reply(200, { ok: true });
	await request('GET', '/api/system', { background: true });
	expect(sentHeaders().get('X-Bindaems-Background')).toBe('1');
	expect(sentHeaders().get('X-CSRF-Token')).toBeNull();
});

it('meldet die Sperre mit Wartezeit', async () => {
	reply(429, { detail: 'Zu viele Fehlversuche – Anmeldung gesperrt bis 10:15 Uhr' }, {
		'Retry-After': '900'
	});
	const error = await request('POST', '/api/auth/password', { body: {} }).catch((e) => e);
	expect(error).toBeInstanceOf(ApiError);
	expect([error.status, error.detail, error.retryAfterS]).toEqual([
		429,
		'Zu viele Fehlversuche – Anmeldung gesperrt bis 10:15 Uhr',
		900
	]);
});

it('ruft bei 401 die Anmeldung auf, außer es ist abgeschaltet', async () => {
	const handler = vi.fn();
	onUnauthorized(handler);
	reply(401, { detail: 'Nicht angemeldet' });
	await request('GET', '/api/system').catch(() => undefined);
	reply(401, { detail: 'Benutzername oder Passwort falsch' });
	await request('POST', '/api/auth/login', { body: {}, redirectOn401: false }).catch(() => undefined);
	expect(handler).toHaveBeenCalledTimes(1);
});

it('weist Antworten zurück, die nicht zum Schema passen', async () => {
	reply(200, { user: { id: 'x' } });
	const schema = v.object({ user: v.object({ id: v.number() }) });
	const error = await request('GET', '/api/auth/me', { schema }).catch((e) => e);
	expect(error.detail).toBe(CONTRACT_ERROR);
});

it('meldet eine fehlende Verbindung', async () => {
	fetchMock.mockRejectedValueOnce(new TypeError('Failed to fetch'));
	const error = await request('GET', '/api/system').catch((e) => e);
	expect([error.status, error.detail]).toEqual([0, NETWORK_ERROR]);
});
```

```ts
// ui/src/lib/api/contract.test.ts
import * as v from 'valibot';
import { describe, expect, it } from 'vitest';
import * as s from './schemas';

const files: Record<string, unknown> = import.meta.glob('./contract/*.json', {
	eager: true,
	import: 'default'
});

const SCHEMAS: Record<string, v.GenericSchema> = {
	me: s.MeResponseSchema,
	state: s.StateResponseSchema,
	system: s.SystemResponseSchema,
	settings: s.SettingsCurrentSchema,
	'settings-versions': v.array(s.SettingsMetaSchema),
	limits: s.LimitsSchema,
	consumers: s.ConsumersResponseSchema,
	'consumer-candidates': s.CandidatesSchema,
	'history-catalog': v.array(s.CatalogEntrySchema),
	history: s.HistoryResponseSchema,
	prices: s.PricesResponseSchema,
	'prices-now': s.PricesNowSchema,
	forecast: s.ForecastSchema,
	'ledger-days': v.array(s.DaySummarySchema),
	users: v.array(s.UserSchema),
	audit: v.array(s.AuditEntrySchema),
	'live-hello': s.LiveMessageSchema,
	'live-state': s.LiveMessageSchema,
	'error-422': s.ValidationErrorSchema
};

function issues(schema: v.GenericSchema, value: unknown): string[] {
	const result = v.safeParse(schema, value);
	return result.issues?.map((issue) => `${v.getDotPath(issue)}: ${issue.message}`) ?? [];
}

it('jede Vertragsdatei hat ein Schema', () => {
	const names = Object.keys(files).map((path) => path.slice('./contract/'.length, -'.json'.length));
	expect(names.sort()).toEqual(Object.keys(SCHEMAS).sort());
});

describe.each(Object.entries(SCHEMAS))('%s', (name, schema) => {
	it('passt zum Schema', () => {
		expect(issues(schema, files[`./contract/${name}.json`])).toEqual([]);
	});
});

it('akzeptiert die Nullfälle der API', () => {
	expect(issues(s.StateResponseSchema, { core_connected: false, updated_at: null, state: null, alarms: [] })).toEqual([]);
	expect(issues(s.PricesNowSchema, { now: null, next_3h: [] })).toEqual([]);
	expect(issues(s.SystemResponseSchema, { core: { connected: false, health: null }, components: [], warnings: [] })).toEqual([]);
});
```

- [ ] **Step 2: Tests laufen lassen**

Run: `cd ui && pnpm vitest run src/lib/api`
Expected: FAIL – die Module `errors`, `client` und `schemas` fehlen.

- [ ] **Step 3: Implementieren**

- `errors.ts`: `detailText` – Text unverändert; Liste: je Eintrag `<loc ohne führendes "body", mit Punkten>: <Meldung>`, Zeilen mit `\n` verbunden. Meldungen nach `type`: `float_parsing`, `float_type`, `int_parsing`, `int_type` → „Zahl erwartet“; `missing` → „Angabe fehlt“; `string_too_short` → „zu kurz“; `string_too_long` → „zu lang“; `greater_than`, `greater_than_equal` → „Wert zu klein“; `less_than`, `less_than_equal` → „Wert zu groß“; `literal_error`, `enum` → „Wert nicht erlaubt“; `extra_forbidden` → „unbekanntes Feld“; `json_invalid` → „Ungültiges JSON“; `value_error` → `msg` ohne „Value error, “; sonst `msg`. Alles andere → „Unbekannter Fehler“.
- `client.ts`: `fetch(path, { method, headers, body, signal, credentials: 'same-origin' })`. Header `Accept: application/json`; mit Körper `Content-Type: application/json` und `JSON.stringify`; bei unsicheren Methoden `X-CSRF-Token` aus `csrfToken(document.cookie)`; mit `background` der Hintergrund-Header. 204 → `undefined`. Fehlerstatus → `ApiError(status, detailText(body.detail), body, Retry-After als Zahl oder null)`, bei 401 vorher der registrierte Handler (wenn `redirectOn401 !== false`). Netzfehler → `ApiError(0, NETWORK_ERROR)`; `AbortError` wird weitergeworfen. Mit `schema`: `v.safeParse`, bei Fehlschlag `console.error` mit Pfad und Problemen und `ApiError(status, CONTRACT_ERROR, body)`.
- `schemas.ts`: Formen und Nullbarkeit nach Anhang A, nicht nur nach den Beispielen (ein im Beispiel gefülltes Feld kann `null` sein). `v.object` (unbekannte Felder werden ignoriert, das hält das UI vorwärtskompatibel), `v.picklist` für Aufzählungen, `v.record(v.string(), …)` für Abbildungen, `v.variant('type', …)` für `LiveMessage`, `v.lazy` mit ausdrücklichem Typ für `TreeNode`, Datenpunkte als `v.tuple([v.number(), v.number()])`.
- `endpoints.ts`: Pfade und Parameter nach Anhang A, Abfrageparameter über `URLSearchParams`.

- [ ] **Step 4: Tests laufen lassen**

Run: `cd ui && pnpm vitest run src/lib/api && pnpm check`
Expected: PASS; `svelte-check` ohne Befund.

- [ ] **Step 5: Commit**

```bash
git add ui/src/lib/api
git commit -m "feat(ui): API-Client mit CSRF, Fehlertexten und geprüften Antworten"
```

---

### Task 6: Live-Verbindung und Abfragen im Takt

**Files:**
- Create: `ui/src/lib/live.svelte.ts`, `ui/src/lib/resource.svelte.ts`
- Test: `ui/src/lib/live.test.ts`, `ui/src/lib/resource.test.ts`

**Interfaces:**
- Consumes: `LiveMessageSchema`, `CoreState`, `Alarm` (Task 5); `ApiError` (Task 5).
- Produces:
  - `live.svelte.ts`:
    ```ts
    export type LiveStatus = 'connecting' | 'open' | 'reconnecting' | 'unauthorized' | 'forbidden' | 'stopped';
    export const STALE_AFTER_MS = 5000;
    export const RECONNECT_DELAYS_MS = [1000, 2000, 5000, 10000, 30000] as const; // danach immer 30 s
    export interface SocketLike {
    	onopen: ((event: unknown) => void) | null;
    	onmessage: ((event: { data: unknown }) => void) | null;
    	onclose: ((event: { code: number }) => void) | null;
    	close(): void;
    }
    export interface LiveOptions { url?: string; connect?: (url: string) => SocketLike; onUnauthorized?: () => void }
    export class LiveConnection {
    	status: LiveStatus;             // $state
    	coreConnected: boolean;         // $state
    	state: CoreState | null;        // $state.raw
    	alarms: Alarm[];                // $state.raw
    	lastMessageAt: number | null;   // $state
    	readonly stale: boolean;        // $derived: nicht offen, core getrennt, nie oder seit > 5 s keine Nachricht
    	constructor(options?: LiveOptions);
    	start(): void;
    	stop(): void;
    }
    export function liveUrl(location: { protocol: string; host: string }): string;
    export const live: LiveConnection; // eine Verbindung für die ganze App
    ```
  - `resource.svelte.ts`:
    ```ts
    export interface ResourceOptions { intervalMs?: number; hidden?: () => boolean }
    export class Resource<T> {
    	data: T | undefined;            // $state.raw – bleibt bei Fehlern stehen
    	error: ApiError | null;         // $state.raw
    	loading: boolean;               // $state
    	constructor(load: (o: { background: boolean; signal: AbortSignal }) => Promise<T>, options?: ResourceOptions);
    	start(): () => void;            // erste Ladung im Vordergrund, dann im Takt im Hintergrund; gibt stop zurück
    	refresh(): Promise<void>;       // im Vordergrund (Bedienung)
    }
    ```

- [ ] **Step 1: Failing Tests schreiben**

```ts
// ui/src/lib/live.test.ts
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import hello from './api/contract/live-hello.json';
import stateMessage from './api/contract/live-state.json';
import { LiveConnection, RECONNECT_DELAYS_MS, STALE_AFTER_MS, liveUrl } from './live.svelte';

class FakeSocket {
	onopen: ((event: unknown) => void) | null = null;
	onmessage: ((event: { data: unknown }) => void) | null = null;
	onclose: ((event: { code: number }) => void) | null = null;
	close() {}
	open() {
		this.onopen?.({});
	}
	send(message: unknown) {
		this.onmessage?.({ data: JSON.stringify(message) });
	}
	drop(code = 1006) {
		this.onclose?.({ code });
	}
}

let sockets: FakeSocket[] = [];

function connect(onUnauthorized = vi.fn()) {
	sockets = [];
	const live = new LiveConnection({
		url: 'ws://test/api/live',
		connect: () => {
			const socket = new FakeSocket();
			sockets.push(socket);
			return socket;
		},
		onUnauthorized
	});
	live.start();
	return live;
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

it('übernimmt hello, state und alarm', () => {
	const live = connect();
	sockets[0].open();
	sockets[0].send(hello);
	expect([live.status, live.coreConnected]).toEqual(['open', true]);
	sockets[0].send(stateMessage);
	expect(live.state?.derived.pv_total_w).toBe(stateMessage.data.derived.pv_total_w);
	const alarm = { id: 'competitor.dess', severity: 'warning', message: 'DESS aktiv', since: '2026-10-09T08:00:00+00:00' };
	sockets[0].send({ type: 'alarm', data: [alarm] });
	expect(live.alarms.map((a) => a.id)).toEqual(['competitor.dess']);
});

it('markiert Daten nach 5 s ohne Nachricht als veraltet', () => {
	const live = connect();
	sockets[0].open();
	sockets[0].send(hello);
	expect(live.stale).toBe(false);
	vi.advanceTimersByTime(STALE_AFTER_MS + 1000);
	expect(live.stale).toBe(true);
	sockets[0].send(stateMessage);
	expect(live.stale).toBe(false);
});

it('meldet einen getrennten core als veraltet', () => {
	const live = connect();
	sockets[0].open();
	sockets[0].send(hello);
	sockets[0].send({ type: 'core', data: { connected: false } });
	expect([live.coreConnected, live.stale]).toEqual([false, true]);
});

it('verbindet mit 1, 2, 5, 10, 30 s Abstand neu', () => {
	connect();
	for (const delay of [...RECONNECT_DELAYS_MS, 30_000]) {
		sockets.at(-1)?.drop();
		const before = sockets.length;
		vi.advanceTimersByTime(delay - 1);
		expect(sockets.length).toBe(before);
		vi.advanceTimersByTime(1);
		expect(sockets.length).toBe(before + 1);
	}
});

it('beginnt nach einer gelungenen Verbindung wieder mit 1 s', () => {
	connect();
	sockets[0].drop();
	vi.advanceTimersByTime(1000);
	sockets[1].drop();
	vi.advanceTimersByTime(2000);
	sockets[2].open();
	sockets[2].send(hello);
	sockets[2].drop();
	vi.advanceTimersByTime(1000);
	expect(sockets).toHaveLength(4);
});

it('4401 meldet die abgelaufene Sitzung und verbindet nicht neu', () => {
	const onUnauthorized = vi.fn();
	const live = connect(onUnauthorized);
	sockets[0].drop(4401);
	vi.advanceTimersByTime(60_000);
	expect(live.status).toBe('unauthorized');
	expect(onUnauthorized).toHaveBeenCalledOnce();
	expect(sockets).toHaveLength(1);
});

it('4403 bleibt getrennt', () => {
	const live = connect();
	sockets[0].drop(4403);
	vi.advanceTimersByTime(60_000);
	expect([live.status, sockets.length]).toEqual(['forbidden', 1]);
});

it('übergeht kaputte Nachrichten', () => {
	const live = connect();
	sockets[0].open();
	sockets[0].send(hello);
	sockets[0].onmessage?.({ data: 'kein JSON' });
	sockets[0].send({ type: 'unbekannt', data: 1 });
	expect(live.state).toEqual(hello.data.state);
});

it('baut die Adresse aus dem Ursprung der Seite', () => {
	expect(liveUrl({ protocol: 'https:', host: 'ems.lan' })).toBe('wss://ems.lan/api/live');
	expect(liveUrl({ protocol: 'http:', host: '127.0.0.1:8099' })).toBe('ws://127.0.0.1:8099/api/live');
});
```

```ts
// ui/src/lib/resource.test.ts
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ApiError } from './api/errors';
import { Resource } from './resource.svelte';

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

it('lädt zuerst im Vordergrund, danach im Takt im Hintergrund', async () => {
	const calls: boolean[] = [];
	const resource = new Resource(
		async ({ background }) => {
			calls.push(background);
			return calls.length;
		},
		{ intervalMs: 15_000, hidden: () => false }
	);
	const stop = resource.start();
	await vi.advanceTimersByTimeAsync(30_000);
	expect(calls).toEqual([false, true, true]);
	expect(resource.data).toBe(3);
	stop();
});

it('fragt in verborgenen Tabs nicht ab und holt beim Zurückkehren nach', async () => {
	let hidden = false;
	const calls: boolean[] = [];
	const resource = new Resource(
		async ({ background }) => {
			calls.push(background);
			return 1;
		},
		{ intervalMs: 15_000, hidden: () => hidden }
	);
	const stop = resource.start();
	await vi.advanceTimersByTimeAsync(0);
	hidden = true;
	await vi.advanceTimersByTimeAsync(60_000);
	expect(calls).toEqual([false]);
	hidden = false;
	document.dispatchEvent(new Event('visibilitychange'));
	await vi.advanceTimersByTimeAsync(0);
	expect(calls).toEqual([false, true]);
	stop();
});

it('behält die letzten Daten bei einem Fehler', async () => {
	const load = vi
		.fn()
		.mockResolvedValueOnce('alt')
		.mockRejectedValueOnce(new ApiError(502, 'InfluxDB nicht erreichbar'))
		.mockResolvedValueOnce('neu');
	const resource = new Resource(load, { intervalMs: 1000, hidden: () => false });
	const stop = resource.start();
	await vi.advanceTimersByTimeAsync(1000);
	expect([resource.data, resource.error?.detail]).toEqual(['alt', 'InfluxDB nicht erreichbar']);
	await vi.advanceTimersByTimeAsync(1000);
	expect([resource.data, resource.error]).toEqual(['neu', null]);
	stop();
});

it('stop bricht die laufende Anfrage ab', async () => {
	let signal: AbortSignal | undefined;
	const resource = new Resource(({ signal: s }) => {
		signal = s;
		return new Promise(() => {});
	});
	const stop = resource.start();
	stop();
	expect(signal?.aborted).toBe(true);
});
```

- [ ] **Step 2: Tests laufen lassen**

Run: `cd ui && pnpm vitest run src/lib/live.test.ts src/lib/resource.test.ts`
Expected: FAIL – Module fehlen.

- [ ] **Step 3: Implementieren**

`LiveConnection`: `start()` öffnet über `connect(url)` (Standard: `new WebSocket(liveUrl(location))`) und startet einen 1-s-Takt, der eine interne Uhr (`$state`) für `stale` weiterschaltet. `onmessage`: `JSON.parse` und `v.safeParse(LiveMessageSchema)`; Ungültiges wird verworfen. `hello` setzt Zustand, Alarme und core-Verbindung; `state` setzt den Zustand; `alarm` die Liste; `core` die Verbindung. Jede gültige Nachricht setzt `lastMessageAt`, und nach `hello` beginnt die Wartezeit wieder bei 1 s. `onclose`: 4401 → `unauthorized` und Handler; 4403 → `forbidden`; sonst `reconnecting` und neuer Versuch nach der nächsten Wartezeit. `stop()` schließt und räumt alle Zeitgeber ab. `Resource`: ein `AbortController` je Ladung; Takt per `setTimeout` nach dem Ende der vorigen Ladung; `hidden` Standard `() => document.visibilityState === 'hidden'`; `visibilitychange` löst bei Sichtbarkeit eine Hintergrundladung aus; `AbortError` setzt keinen Fehler.

- [ ] **Step 4: Tests laufen lassen**

Run: `cd ui && pnpm vitest run src/lib && pnpm check`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ui/src/lib/live.svelte.ts ui/src/lib/resource.svelte.ts ui/src/lib/live.test.ts ui/src/lib/resource.test.ts
git commit -m "feat(ui): Live-Verbindung mit Neuverbinden und Abfragen im Takt"
```

---

### Task 7: Anmeldung und Sitzung

**Files:**
- Create: `ui/src/lib/roles.ts`, `ui/src/lib/navigation.ts`, `ui/src/lib/auth/LoginForm.svelte`, `ui/src/routes/login/+page.svelte`, `ui/src/routes/+error.svelte`, `ui/src/routes/+layout.svelte` (vorerst nur Anmelde-Weiche und 401-Handler; Task 8 baut den Rahmen)
- Modify: `ui/src/routes/+layout.ts`
- Test: `ui/src/lib/roles.test.ts`, `ui/src/lib/navigation.test.ts`, `ui/src/lib/auth/LoginForm.test.ts`, `ui/src/routes/layout.test.ts`

**Interfaces:**
- Consumes: `api.me`, `api.login`, `ApiError`, `onUnauthorized` (Task 5); `live` (Task 6).
- Produces:
  - `roles.ts`: `ROLE_RANK: Record<Role, number>` (viewer 0, operator 1, admin 2), `ROLE_LABELS: Record<Role, string>` (Admin, Bedienen, Lesen), `hasRole(user: User | null | undefined, role: Role): boolean`, `requireAdmin(user: User | null | undefined): void` (wirft `error(403, 'Keine Berechtigung')` aus `@sveltejs/kit`)
  - `navigation.ts`: `safeNext(next: string | null | undefined): string` (nur Pfade dieser App: beginnt mit `/`, nicht mit `//` oder `/\`; sonst `/`), `loginHref(url: URL, expired?: boolean): string` (`/login?next=<Pfad mit Query, kodiert>`, mit `expired` zusätzlich `&abgelaufen=1`)
  - `+layout.ts`: `load` liefert `{ user: User | null }`; auf `/login` ohne Anfrage `null`; sonst `api.me({ redirectOn401: false })`, bei 401 `redirect(307, loginHref(url))`
  - `LoginForm.svelte`: Props `{ login: (body: LoginBody) => Promise<User>; onSuccess: (user: User) => void }`; Felder „Benutzername“, „Passwort“, „Angemeldet bleiben“ (Hinweis: „gilt nicht für Admins“) und – nach `totp_required` – „Bestätigungscode“ (`inputmode="numeric"`, `autocomplete="one-time-code"`)

- [ ] **Step 1: Failing Tests schreiben**

```ts
// ui/src/lib/navigation.test.ts
import { expect, it } from 'vitest';
import { loginHref, safeNext } from './navigation';

it('leitet nur auf Pfade dieser App weiter', () => {
	expect(safeNext('/verlauf?tag=2026-10-09')).toBe('/verlauf?tag=2026-10-09');
	for (const bad of ['//boese.example', 'https://boese.example', '/\\boese.example', '', null]) {
		expect(safeNext(bad)).toBe('/');
	}
});

it('401 führt zur Anmeldung mit abgelaufen=1', () => {
	const url = new URL('https://ems.lan/system?ansicht=signale');
	expect(loginHref(url)).toBe('/login?next=%2Fsystem%3Fansicht%3Dsignale');
	expect(loginHref(url, true)).toBe('/login?next=%2Fsystem%3Fansicht%3Dsignale&abgelaufen=1');
});
```

```ts
// ui/src/lib/roles.test.ts
import { expect, it } from 'vitest';
import { hasRole } from './roles';

const user = (role: 'admin' | 'operator' | 'viewer') => ({
	id: 1,
	username: 'x',
	role,
	totp_enabled: false,
	created_at: '2026-10-09T08:00:00+00:00'
});

it('ordnet die Rollen Lesen < Bedienen < Admin', () => {
	expect(hasRole(user('admin'), 'operator')).toBe(true);
	expect(hasRole(user('operator'), 'operator')).toBe(true);
	expect(hasRole(user('viewer'), 'operator')).toBe(false);
	expect(hasRole(null, 'viewer')).toBe(false);
});
```

```ts
// ui/src/lib/auth/LoginForm.test.ts
import { fireEvent, render, screen } from '@testing-library/svelte';
import { expect, it, vi } from 'vitest';
import { ApiError } from '$lib/api/errors';
import LoginForm from './LoginForm.svelte';

const user = { id: 2, username: 'sicher', role: 'admin', totp_enabled: true, created_at: '2026-10-09T08:00:00+00:00' };
const totpRequired = new ApiError(401, 'Bestätigungscode erforderlich', {
	detail: 'Bestätigungscode erforderlich',
	totp_required: true
});

async function submit(username: string, password: string) {
	await fireEvent.input(screen.getByLabelText('Benutzername'), { target: { value: username } });
	await fireEvent.input(screen.getByLabelText('Passwort'), { target: { value: password } });
	await fireEvent.click(screen.getByRole('button', { name: 'Anmelden' }));
}

it('fragt nach dem Code, wenn TOTP aktiv ist', async () => {
	const login = vi.fn().mockRejectedValueOnce(totpRequired).mockResolvedValueOnce(user);
	const onSuccess = vi.fn();
	render(LoginForm, { login, onSuccess });
	await submit('sicher', 'demo-passwort-1');
	await fireEvent.input(await screen.findByLabelText('Bestätigungscode'), {
		target: { value: '123456' }
	});
	await fireEvent.click(screen.getByRole('button', { name: 'Anmelden' }));
	expect(login).toHaveBeenLastCalledWith({
		username: 'sicher',
		password: 'demo-passwort-1',
		totp: '123456',
		remember: false
	});
	expect(onSuccess).toHaveBeenCalledWith(user);
});

it('behält das Codefeld nach einem falschen Code', async () => {
	const wrong = new ApiError(401, 'Benutzername oder Passwort falsch', {
		detail: 'Benutzername oder Passwort falsch'
	});
	const login = vi.fn().mockRejectedValueOnce(totpRequired).mockRejectedValueOnce(wrong);
	render(LoginForm, { login, onSuccess: vi.fn() });
	await submit('sicher', 'demo-passwort-1');
	await fireEvent.input(await screen.findByLabelText('Bestätigungscode'), {
		target: { value: '000000' }
	});
	await fireEvent.click(screen.getByRole('button', { name: 'Anmelden' }));
	expect(await screen.findByText('Benutzername oder Passwort falsch')).toBeInTheDocument();
	expect(screen.getByLabelText('Bestätigungscode')).toBeInTheDocument();
});

it('zeigt die Sperre mit Uhrzeit', async () => {
	const locked = new ApiError(429, 'Zu viele Fehlversuche – Anmeldung gesperrt bis 10:15 Uhr', {}, 900);
	render(LoginForm, { login: vi.fn().mockRejectedValueOnce(locked), onSuccess: vi.fn() });
	await submit('gast', 'falsch-falsch');
	expect(
		await screen.findByText('Zu viele Fehlversuche – Anmeldung gesperrt bis 10:15 Uhr')
	).toBeInTheDocument();
});
```

```ts
// ui/src/routes/layout.test.ts
import { expect, it, vi } from 'vitest';
import { ApiError } from '$lib/api/errors';

const { me } = vi.hoisted(() => ({ me: vi.fn() })); // vi.mock wird an den Dateianfang gehoben
vi.mock('$lib/api/endpoints', () => ({ api: { me } }));
const { load } = await import('./+layout');

it('leitet ohne Sitzung zur Anmeldung und merkt sich das Ziel', async () => {
	me.mockRejectedValueOnce(new ApiError(401, 'Nicht angemeldet'));
	const url = new URL('https://ems.lan/system');
	await expect(load({ url } as never)).rejects.toMatchObject({
		status: 307,
		location: '/login?next=%2Fsystem'
	});
});

it('fragt auf der Anmeldeseite nicht nach dem Benutzer', async () => {
	me.mockClear();
	await expect(load({ url: new URL('https://ems.lan/login') } as never)).resolves.toEqual({ user: null });
	expect(me).not.toHaveBeenCalled();
});
```

- [ ] **Step 2: Tests laufen lassen**

Run: `cd ui && pnpm vitest run src/lib/roles.test.ts src/lib/navigation.test.ts src/lib/auth src/routes`
Expected: FAIL – Module und Komponente fehlen.

- [ ] **Step 3: Implementieren**

- `LoginForm.svelte`: ein `<form>` mit `onsubmit` (`preventDefault`), Felder mit `<label>` (für `getByLabelText`), Fehlermeldung in einem Bereich mit `role="alert"`. `totp_required` im `body` des `ApiError` blendet das Codefeld ein; es bleibt bis zum Erfolg sichtbar. Der Knopf ist während der Anfrage gesperrt.
- `login/+page.svelte`: `LoginForm` mit `login = (b) => api.login(b).then((r) => r.user)`, nach Erfolg `goto(safeNext(next), { replaceState: true, invalidateAll: true })`; Hinweis „Sitzung abgelaufen – bitte neu anmelden.“ bei `abgelaufen=1`. Seitentitel „Anmelden – BindaEMS“.
- `+layout.svelte`: registriert in `onMount` `onUnauthorized(() => goto(loginHref(page.url, true), { replaceState: true }))` und dieselbe Funktion als `onUnauthorized` der Live-Verbindung; rendert vorerst nur `{@render children()}`.
- `+error.svelte`: 403 „Keine Berechtigung“, 404 „Seite nicht gefunden“, sonst `page.error?.message`; Link zur Übersicht.

- [ ] **Step 4: Tests laufen lassen**

Run: `cd ui && pnpm test && pnpm check`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ui/src
git commit -m "feat(ui): Anmeldung mit TOTP, Sitzungsweiche und Rollen"
```

---

### Task 8: App-Rahmen: Navigation, Darstellung, Live-Anzeige, PWA

**Files:**
- Create: `ui/src/lib/nav.ts`, `ui/src/lib/theme.svelte.ts`, `ui/src/lib/LiveBadge.svelte`, `ui/src/lib/components/Card.svelte`, `Notice.svelte`, `StatusDot.svelte`, `Icon.svelte`, `Dialog.svelte`, `ui/static/manifest.webmanifest`, `ui/static/icon.svg`, `ui/static/favicon.svg`, `ui/static/icons/icon-192.png`, `icon-512.png`, `icon-512-maskable.png`, `apple-touch-icon.png`, `ui/scripts/render-icons.mjs`
- Modify: `ui/src/routes/+layout.svelte` (Rahmen), `ui/src/app.html`, `ui/src/app.css`
- Test: `ui/src/lib/nav.test.ts`, `ui/src/lib/theme.test.ts`, `ui/src/lib/manifest.test.ts`, `ui/src/lib/LiveBadge.test.ts`

**Interfaces:**
- Consumes: `live`, `LiveStatus` (Task 6); `page.data.user` aus `+layout.ts`, `loginHref` (Task 7); `api.logout` (Task 5).
- Produces:
  - `nav.ts`: `type IconName = 'overview' | 'history' | 'consumers' | 'system' | 'settings' | 'users' | 'audit' | 'account' | 'menu' | 'theme' | 'pv' | 'grid' | 'battery' | 'house' | 'wallbox' | 'car'`, `interface NavItem { href: string; label: string; icon: IconName }`, `navItems(role: Role): NavItem[]`
  - `theme.svelte.ts`: `type ThemePref = 'system' | 'light' | 'dark'`, `THEME_KEY = 'bindaems.theme'`, `readThemePref(storage?: Pick<Storage, 'getItem'> | null): ThemePref`, `nextThemePref(pref: ThemePref): ThemePref`, `theme: { readonly pref: ThemePref; readonly dark: boolean; set(pref: ThemePref): void }` (`dark` folgt der Wahl oder `prefers-color-scheme`)
  - Komponenten: `Card` (`title?: string`, `children`), `Notice` (`level: 'error' | 'warning' | 'info'`, `children`), `StatusDot` (`state: 'ok' | 'warn' | 'error' | 'unknown'`, `label: string`), `Icon` (`name: IconName`), `Dialog` (`open: boolean` gebunden, `title: string`, `children`; natives `<dialog>`), `LiveBadge` (`status: LiveStatus`, `stale: boolean`, `coreConnected: boolean`)
  - CSS-Variablen in `app.css`: `--bg`, `--surface`, `--surface-2`, `--text`, `--muted`, `--border`, `--accent`, `--ok`, `--warn`, `--error`, `--pv`, `--grid`, `--battery`, `--house`, `--wallbox`, `--radius`, `--gap`

- [ ] **Step 1: Failing Tests schreiben**

```ts
// ui/src/lib/nav.test.ts
import { expect, it } from 'vitest';
import { navItems } from './nav';

it('zeigt Lesenden und Bedienenden dieselben Seiten', () => {
	expect(navItems('viewer').map((item) => [item.href, item.label])).toEqual([
		['/', 'Übersicht'],
		['/verlauf', 'Verlauf'],
		['/verbraucher', 'Verbraucher'],
		['/system', 'System'],
		['/einstellungen', 'Einstellungen'],
		['/konto', 'Konto']
	]);
	expect(navItems('operator')).toEqual(navItems('viewer'));
});

it('zeigt Admins zusätzlich Benutzer und Protokoll', () => {
	expect(navItems('admin').map((item) => item.href)).toEqual([
		'/',
		'/verlauf',
		'/verbraucher',
		'/system',
		'/einstellungen',
		'/benutzer',
		'/protokoll',
		'/konto'
	]);
});
```

```ts
// ui/src/lib/theme.test.ts
import { expect, it } from 'vitest';
import { nextThemePref, readThemePref, theme } from './theme.svelte';

it('liest nur gültige Werte', () => {
	expect(readThemePref({ getItem: () => 'dark' })).toBe('dark');
	expect(readThemePref({ getItem: () => 'pink' })).toBe('system');
	expect(readThemePref(null)).toBe('system');
});

it('wechselt System → Hell → Dunkel → System', () => {
	expect((['system', 'light', 'dark'] as const).map(nextThemePref)).toEqual([
		'light',
		'dark',
		'system'
	]);
});

it('setzt data-theme und merkt sich die Wahl', () => {
	theme.set('dark');
	expect([document.documentElement.dataset.theme, localStorage.getItem('bindaems.theme')]).toEqual([
		'dark',
		'dark'
	]);
	expect(theme.dark).toBe(true);
	theme.set('system');
	expect(document.documentElement.dataset.theme).toBeUndefined();
	expect(localStorage.getItem('bindaems.theme')).toBeNull();
});
```

```ts
// ui/src/lib/manifest.test.ts
import { readFileSync } from 'node:fs';
import { expect, it } from 'vitest';

interface Icon { src: string; sizes: string; type: string; purpose?: string }
const manifest = JSON.parse(readFileSync('static/manifest.webmanifest', 'utf8'));

function pngSize(path: string): string {
	const bytes = readFileSync(path);
	return `${bytes.readUInt32BE(16)}x${bytes.readUInt32BE(20)}`;
}

it('beschreibt eine installierbare App', () => {
	expect(manifest).toMatchObject({
		name: 'BindaEMS',
		short_name: 'BindaEMS',
		lang: 'de',
		start_url: '/',
		scope: '/',
		display: 'standalone'
	});
	const icons: Icon[] = manifest.icons;
	expect(icons.map((icon) => icon.sizes)).toEqual(expect.arrayContaining(['192x192', '512x512']));
	expect(icons.some((icon) => icon.purpose === 'maskable')).toBe(true);
	for (const icon of icons.filter((icon) => icon.type === 'image/png')) {
		expect(pngSize(`static${icon.src}`)).toBe(icon.sizes);
	}
});

it('verlinkt Manifest, Icons und Theme-Farbe in app.html', () => {
	const html = readFileSync('src/app.html', 'utf8');
	for (const part of ['rel="manifest"', 'rel="apple-touch-icon"', 'rel="icon"', 'name="theme-color"']) {
		expect(html).toContain(part);
	}
});
```

```ts
// ui/src/lib/LiveBadge.test.ts
import { render, screen } from '@testing-library/svelte';
import { expect, it } from 'vitest';
import LiveBadge from './LiveBadge.svelte';

it.each([
	[{ status: 'open', stale: false, coreConnected: true }, 'live'],
	[{ status: 'open', stale: true, coreConnected: false }, 'core getrennt'],
	[{ status: 'open', stale: true, coreConnected: true }, 'veraltet'],
	[{ status: 'reconnecting', stale: true, coreConnected: true }, 'verbinde …'],
	[{ status: 'unauthorized', stale: true, coreConnected: false }, 'abgemeldet']
] as const)('zeigt %o als „%s“', (props, text) => {
	render(LiveBadge, props);
	expect(screen.getByText(text)).toBeInTheDocument();
});
```

- [ ] **Step 2: Tests laufen lassen**

Run: `cd ui && pnpm vitest run src/lib/nav.test.ts src/lib/theme.test.ts src/lib/manifest.test.ts src/lib/LiveBadge.test.ts`
Expected: FAIL – Module, Komponente und Manifest fehlen.

- [ ] **Step 3: Implementieren**

- `theme.svelte.ts`: `$state` für Wahl und System-Dunkel (`window.matchMedia?.('(prefers-color-scheme: dark)')`, in jsdom fehlt `matchMedia`); `set` schreibt bzw. entfernt `data-theme` und den Speicherschlüssel; Speicherzugriffe in `try/catch` (privater Modus).
- `app.html`: im `<head>` vor `%sveltekit.head%` ein Inline-Skript, das `bindaems.theme` liest und bei `light`/`dark` `document.documentElement.dataset.theme` setzt (ohne Flackern); `<link rel="manifest" href="/manifest.webmanifest">`, `<link rel="icon" href="/favicon.svg" type="image/svg+xml">`, `<link rel="apple-touch-icon" href="/icons/apple-touch-icon.png">`, `<meta name="theme-color" content="…">` für hell und dunkel (zwei Tags mit `media`).
- `app.css`: Variablen für hell als Standard, dunkel über `:root[data-theme='dark']` und `@media (prefers-color-scheme: dark) { :root:not([data-theme='light']) { … } }`; Systemschrift, sichtbarer Fokus, `@media (prefers-reduced-motion: reduce)` ohne Animationen, Sicherheitsabstände `env(safe-area-inset-*)`.
- `manifest.webmanifest`: `name`, `short_name`, `lang: "de"`, `start_url: "/"`, `scope: "/"`, `display: "standalone"`, `background_color`, `theme_color`, Icons: `/icon.svg` (`sizes: "any"`), `/icons/icon-192.png`, `/icons/icon-512.png`, `/icons/icon-512-maskable.png` (`purpose: "maskable"`).
- `scripts/render-icons.mjs`: rendert `static/icon.svg` mit `chromium` aus `@playwright/test` als PNG in 192, 512, 512 maskierbar (Motiv auf 80 % verkleinert, Hintergrund in Akzentfarbe) und 180 (Apple); einmal `node scripts/render-icons.mjs` ausführen und die PNGs einchecken.
- `+layout.svelte`: Auf `/login` nur der Inhalt (die Anmeldeseite hat ihre eigene `<h1>` „Anmelden“). Sonst Kopfzeile mit dem Seitentitel aus `navItems` als einziger `<h1>` der Seite, `LiveBadge`, Darstellungs-Knopf (`aria-label` „Darstellung: System/Hell/Dunkel“) und Abmelden (`api.logout()`, dann `goto('/login')`). Navigation ab 960 px als Seitenleiste, darunter über einen Menü-Knopf in `Dialog`. Startet `live` bei angemeldetem Benutzer und stoppt beim Verlassen. `<svelte:head><title>…</title></svelte:head>` je Seite über den Titel aus `navItems`.

- [ ] **Step 4: Tests und Build laufen lassen**

Run: `cd ui && pnpm test && pnpm lint && pnpm check && pnpm build && grep -c '<script' build/index.html`
Expected: PASS; Ausgabe `2` (Startskript und Theme-Skript).

- [ ] **Step 5: Commit**

```bash
git add ui
git commit -m "feat(ui): App-Rahmen mit Navigation, hell und dunkel, Live-Anzeige und Manifest"
```

---

### Task 9: Übersicht – Energiefluss, Ladestände, Hinweise, Verbraucher

**Files:**
- Create: `ui/src/lib/dashboard/flow.ts`, `ui/src/lib/dashboard/EnergyFlow.svelte`, `ui/src/lib/dashboard/socs.ts`, `ui/src/lib/dashboard/SocList.svelte`, `ui/src/lib/dashboard/notices.ts`, `ui/src/lib/dashboard/Notices.svelte`, `ui/src/lib/dashboard/ConsumerSummary.svelte`, `ui/src/lib/consumers/tree.ts`, `ui/src/lib/testing/fixtures.ts`
- Modify: `ui/src/routes/+page.svelte`
- Test: `ui/src/lib/dashboard/flow.test.ts`, `ui/src/lib/dashboard/EnergyFlow.test.ts`, `ui/src/lib/dashboard/socs.test.ts`, `ui/src/lib/dashboard/notices.test.ts`, `ui/src/lib/consumers/tree.test.ts`

**Interfaces:**
- Consumes: `live` (Task 6), `Resource` (Task 6), `api.limits`, `api.system`, `api.consumers` und die Typen aus Task 5, `Card`, `Notice` (Task 8), `format.ts` (Task 1).
- Produces:
  - `testing/fixtures.ts` (nur für Tests): `coreState(): CoreState`, `derived(overrides?: Partial<Derived>): Derived`, `reading(v: number | string | boolean | null, q?: 'ok' | 'stale' | 'invalid'): Reading`, `limits(): Limits`, `system(): SystemResponse` – jeweils aus den Vertragsdateien, mit `v.parse` durch das Schema
  - `flow.ts`: `IDLE_W = 20`, `type FlowDirection = 'in' | 'out' | 'idle' | 'unknown'` (`in` = zum Hausanschluss hin), `interface FlowBranch { id: string; label: string; caption: string; powerW: number | null; direction: FlowDirection }`, `wallboxLabels(limits: Limits | null | undefined): Record<string, string>` (`victron_evcs_ns` → „EVCS“, `tesla_wall_connector_gen3` → „Wall Connector“), `flowBranches(derived: Derived | null | undefined, wallboxes: Record<string, string>): FlowBranch[]` (Reihenfolge PV, Netz, Akku, Haus, Wallboxen nach Schlüssel; `powerW` ohne Vorzeichen, die Richtung trägt es)
  - `EnergyFlow.svelte`: Props `{ branches: FlowBranch[]; stale: boolean }`; `<figure data-stale>` mit SVG-Stern, je Zweig `<g data-testid="flow-<id>" data-direction>`
  - `socs.ts`: `type SocQuality = 'ok' | 'stale' | 'missing'`, `interface SocEntry { id: string; label: string; pct: number | null; quality: SocQuality }`, `socEntries(state: CoreState | null | undefined, limits: Limits | null | undefined): SocEntry[]` (Akku aus `battery.soc_pct`, dann die Fahrzeuge in der Reihenfolge von `limits.vehicles` aus `vehicle.<key>.soc_pct`; `invalid` gilt als fehlend)
  - `notices.ts`: `interface Notice { level: 'error' | 'warning' | 'info'; text: string }`, `noticesFrom(system: SystemResponse | undefined): Notice[]` (Alarme des core nach Schwere, dann Komponenten mit `ok: false`, dann `warnings`; Fehler vor Warnungen vor Hinweisen; gleiche Texte nur einmal)
  - `consumers/tree.ts`: `interface ConsumerRow { key: string; id: number | null; name: string; depth: number; powerW: number | null; color: string | null; kind: 'root' | 'consumer' | 'other'; mismatch: boolean }`, `consumerRows(tree: TreeNode): ConsumerRow[]` (Tiefensuche; nach den Kindern jedes Knotens mit Kindern eine Zeile „Sonstiges“ mit `other_w` und `mismatch` des Knotens)

- [ ] **Step 1: Failing Tests schreiben**

```ts
// ui/src/lib/dashboard/flow.test.ts
import { expect, it } from 'vitest';
import { derived, limits } from '$lib/testing/fixtures';
import { flowBranches, wallboxLabels } from './flow';

const labels = { evcs: 'EVCS', twc: 'Wall Connector' };
const midday = derived({
	pv_total_w: 3000,
	grid_w: 512,
	battery_w: -200,
	house_load_w: 1800,
	wallbox_w: { evcs: 0, twc: 1380 }
});

it('benennt Wallboxen nach ihrem Typ', () => {
	expect(wallboxLabels(limits())).toEqual(labels);
});

it('zeigt Quellen zum Hausanschluss hin und Verbraucher von ihm weg', () => {
	expect(flowBranches(midday, labels)).toEqual([
		{ id: 'pv', label: 'PV', caption: 'PV', powerW: 3000, direction: 'in' },
		{ id: 'grid', label: 'Netz', caption: 'Bezug', powerW: 512, direction: 'in' },
		{ id: 'battery', label: 'Akku', caption: 'entlädt', powerW: 200, direction: 'in' },
		{ id: 'house', label: 'Haus', caption: 'Haus', powerW: 1800, direction: 'out' },
		{ id: 'wallbox:evcs', label: 'EVCS', caption: 'EVCS', powerW: 0, direction: 'idle' },
		{ id: 'wallbox:twc', label: 'Wall Connector', caption: 'Wall Connector', powerW: 1380, direction: 'out' }
	]);
});

it('kennt Einspeisung und Laden', () => {
	const [, grid, battery] = flowBranches({ ...midday, grid_w: -1500, battery_w: 2500 }, labels);
	expect([grid.caption, grid.direction, grid.powerW]).toEqual(['Einspeisung', 'out', 1500]);
	expect([battery.caption, battery.direction]).toEqual(['lädt', 'out']);
});

it('unbekannte Werte zeigen einen Strich', () => {
	const branches = flowBranches({ ...midday, grid_w: null, wallbox_w: { evcs: 0 } }, labels);
	expect(branches.find((b) => b.id === 'grid')).toMatchObject({
		powerW: null,
		direction: 'unknown',
		caption: 'Netz'
	});
	expect(branches.find((b) => b.id === 'wallbox:twc')).toMatchObject({ powerW: null, direction: 'unknown' });
	expect(flowBranches(null, labels).every((b) => b.direction === 'unknown')).toBe(true);
});
```

```ts
// ui/src/lib/dashboard/EnergyFlow.test.ts
import { render, screen } from '@testing-library/svelte';
import { expect, it } from 'vitest';
import { derived } from '$lib/testing/fixtures';
import EnergyFlow from './EnergyFlow.svelte';
import { flowBranches } from './flow';

const labels = { evcs: 'EVCS', twc: 'Wall Connector' };

it('zeigt Leistung und Richtung je Zweig', () => {
	const branches = flowBranches(derived({ pv_total_w: 3000, grid_w: 512 }), labels);
	render(EnergyFlow, { branches, stale: false });
	expect(screen.getByTestId('flow-pv')).toHaveAttribute('data-direction', 'in');
	expect(screen.getByTestId('flow-pv')).toHaveTextContent('3,00 kW');
	expect(screen.getByTestId('flow-grid')).toHaveTextContent('512 W');
});

it('veraltete Daten grauen den Energiefluss aus', () => {
	render(EnergyFlow, { branches: flowBranches(derived(), labels), stale: true });
	expect(screen.getByRole('figure')).toHaveAttribute('data-stale', 'true');
	expect(screen.getByText('veraltet')).toBeInTheDocument();
});

it('zeigt unbekannte Werte als Strich, nicht als 0', () => {
	render(EnergyFlow, { branches: flowBranches(derived({ grid_w: null }), labels), stale: false });
	expect(screen.getByTestId('flow-grid')).toHaveTextContent('–');
	expect(screen.getByTestId('flow-grid')).not.toHaveTextContent('0 W');
});
```

```ts
// ui/src/lib/dashboard/socs.test.ts
import { expect, it } from 'vitest';
import { coreState, limits, reading } from '$lib/testing/fixtures';
import { socEntries } from './socs';

function withSignals(signals: Record<string, ReturnType<typeof reading>>) {
	return { ...coreState(), signals };
}

it('Ladestände tragen ihre Qualität', () => {
	const state = withSignals({
		'battery.soc_pct': reading(55),
		'vehicle.tesla.soc_pct': reading(70, 'stale')
	});
	expect(socEntries(state, limits())).toEqual([
		{ id: 'battery', label: 'Akku', pct: 55, quality: 'ok' },
		{ id: 'tesla', label: 'Tesla Model 3', pct: 70, quality: 'stale' },
		{ id: 'egolf', label: 'e-Golf', pct: null, quality: 'missing' }
	]);
});

it('ungültige Werte gelten als fehlend', () => {
	const [battery] = socEntries(withSignals({ 'battery.soc_pct': reading(55, 'invalid') }), limits());
	expect(battery).toEqual({ id: 'battery', label: 'Akku', pct: null, quality: 'missing' });
});

it('ohne Zustand fehlt alles', () => {
	expect(socEntries(null, limits()).every((entry) => entry.quality === 'missing')).toBe(true);
});
```

```ts
// ui/src/lib/dashboard/notices.test.ts
import * as v from 'valibot';
import { expect, it } from 'vitest';
import { SystemResponseSchema } from '$lib/api/schemas';
import { system } from '$lib/testing/fixtures';
import { noticesFrom } from './notices';

const since = '2026-10-09T08:00:00+00:00';

it('ordnet Fehler vor Warnungen und entfernt Doppeltes', () => {
	const base = system();
	const health = base.core.health;
	const data = v.parse(SystemResponseSchema, {
		core: {
			connected: true,
			health: health && {
				...health,
				alarms: [
					{ id: 'competitor.dess', severity: 'warning', message: 'Dynamic ESS regelt mit', since },
					{ id: 'plaus.soc', severity: 'error', message: 'SOC unplausibel', since }
				]
			}
		},
		components: [
			{ name: 'prices', ok: false, message: 'Kein Preis für den aktuellen Slot', since: null, details: {} },
			{ name: 'influx', ok: true, message: 'InfluxDB: alles übertragen', since: null, details: {} }
		],
		warnings: ['Noch kein OeMAG-Monatswert eingetragen.', 'Noch kein OeMAG-Monatswert eingetragen.']
	});
	expect(noticesFrom(data)).toEqual([
		{ level: 'error', text: 'SOC unplausibel' },
		{ level: 'warning', text: 'Dynamic ESS regelt mit' },
		{ level: 'warning', text: 'Kein Preis für den aktuellen Slot' },
		{ level: 'warning', text: 'Noch kein OeMAG-Monatswert eingetragen.' }
	]);
});

it('ohne Systemdaten gibt es keine Hinweise', () => {
	expect(noticesFrom(undefined)).toEqual([]);
});
```

```ts
// ui/src/lib/consumers/tree.test.ts
import { expect, it } from 'vitest';
import type { TreeNode } from '$lib/api/schemas';
import { consumerRows } from './tree';

const node = (id: number, name: string, power: number | null, children: TreeNode[] = [], other: number | null = null, mismatch = false): TreeNode => ({
	id,
	name,
	color: '#4f8cff',
	power_w: power,
	other_w: other,
	mismatch,
	children
});

const tree: TreeNode = {
	...node(0, 'Haus', 1800, [node(3, 'Küche', 120), node(1, 'Obergeschoss', 600, [node(2, 'Büro', 150)], 450)], 1080),
	id: null,
	color: null
};

it('fügt je Ebene mit Unterverbrauchern „Sonstiges“ an', () => {
	expect(consumerRows(tree).map((row) => [row.kind, row.name, row.depth, row.powerW])).toEqual([
		['root', 'Haus', 0, 1800],
		['consumer', 'Küche', 1, 120],
		['consumer', 'Obergeschoss', 1, 600],
		['consumer', 'Büro', 2, 150],
		['other', 'Sonstiges', 2, 450],
		['other', 'Sonstiges', 1, 1080]
	]);
});

it('überträgt die Abweichung auf „Sonstiges“', () => {
	const odd = node(1, 'OG', 100, [node(2, 'Büro', 150)], 0, true);
	expect(consumerRows(odd).at(-1)).toMatchObject({ kind: 'other', powerW: 0, mismatch: true });
});
```

- [ ] **Step 2: Tests laufen lassen**

Run: `cd ui && pnpm vitest run src/lib/dashboard src/lib/consumers`
Expected: FAIL – Module und Komponenten fehlen.

- [ ] **Step 3: Implementieren**

- `flow.ts` nach den Tests: |Leistung| ≤ `IDLE_W` → `idle`; Netz positiv = Bezug (`in`), Akku positiv = Laden (`out`).
- `EnergyFlow.svelte`: Hausanschluss in der Mitte, PV oben, Netz links, Akku unten links, Haus rechts, Wallboxen unten; je Zweig eine Linie mit laufenden Strichen (CSS-Animation von `stroke-dashoffset`, Richtung nach `direction`, Geschwindigkeit in drei Stufen nach Leistung, ohne Animation bei `idle`, `unknown` und `prefers-reduced-motion`). Farben aus den CSS-Variablen `--pv`, `--grid`, `--battery`, `--house`, `--wallbox`. Mit `stale` gedämpft und mit Hinweis „veraltet“. `aria-label` der Figur fasst alle Zweige in einem Satz zusammen.
- `SocList.svelte`: je Eintrag Name, `formatSoc`, Balken; `stale` mit Zusatz „veraltet“, `missing` mit `–`.
- `Notices.svelte`: Liste aus `Notice`-Komponenten; keine Hinweise → „Keine Hinweise“.
- `ConsumerSummary.svelte`: Zeilen aus `consumerRows` bis Tiefe 1 mit Leistung und Anteil am Haus; Link „Alle Verbraucher“ nach `/verbraucher`.
- `+page.svelte` (Übersicht): Karten Energiefluss (`flowBranches(live.state?.derived, wallboxLabels(limits))`, `stale={live.stale}`), Ladestände, Hinweise (`Resource` auf `api.system`, 15 s), Verbraucher (`Resource` auf `api.consumers`, 15 s); `api.limits` einmal. Ressourcen in `onMount` starten und beim Verlassen stoppen. Task 10 ergänzt Preis und Diagramm.

- [ ] **Step 4: Tests laufen lassen**

Run: `cd ui && pnpm test && pnpm check`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ui/src
git commit -m "feat(ui): Übersicht mit Energiefluss, Ladeständen, Hinweisen und Verbrauchern"
```

---

### Task 10: Preise und PV-Prognose

**Files:**
- Create: `ui/src/lib/charts/echarts.ts`, `ui/src/lib/charts/EChart.svelte`, `ui/src/lib/charts/palette.ts`, `ui/src/lib/charts/options.ts`, `ui/src/lib/dashboard/prices.ts`, `ui/src/lib/dashboard/PriceNow.svelte`
- Modify: `ui/src/routes/+page.svelte`
- Test: `ui/src/lib/charts/options.test.ts`, `ui/src/lib/dashboard/prices.test.ts`, `ui/src/lib/dashboard/PriceNow.test.ts`

**Interfaces:**
- Consumes: `api.pricesNow`, `api.prices`, `api.forecast`, `PriceSlot`, `ForecastSlot`, `PricesNow` (Task 5), `Resource` (Task 6), `theme` (Task 8), `format.ts`, `parseIso` (Task 1).
- Produces:
  - `echarts.ts`: `loadECharts(): Promise<typeof import('echarts/core')>` – lädt ECharts erst bei Bedarf (eigener Chunk) und registriert `LineChart`, `BarChart`, `GridComponent`, `TooltipComponent`, `LegendComponent`, `DataZoomComponent`, `MarkLineComponent`, `CanvasRenderer`
  - `EChart.svelte`: Props `{ option: EChartsCoreOption; label: string; height?: string }`
  - `palette.ts`: `interface Palette { text: string; muted: string; grid: string; importPrice: string; feedIn: string; pv: string; series: string[] }`, `readPalette(element?: Element): Palette` (aus den CSS-Variablen)
  - `options.ts`: `SLOT_MS = 900_000`, `priceForecastOption(prices: PriceSlot[], forecast: ForecastSlot[], nowIso: string, palette: Palette): EChartsCoreOption` (Reihen in dieser Reihenfolge: „Bezugspreis“ immer, „Einspeisung“ nur mit mindestens einem OeMAG-Wert, „PV-Prognose“ nur mit Prognose)
  - `prices.ts`: `type PriceLevel = 'low' | 'mid' | 'high'`, `interface PriceSummary { nowCt: number | null; feedInCt: number | null; origin: 'primary' | 'fallback' | null; incomplete: boolean; next: { start: string; ct: number; level: PriceLevel }[] }`, `priceSummary(data: PricesNow): PriceSummary` (Drittel der Spanne der nächsten 3 h; alle gleich → `mid`)
  - `PriceNow.svelte`: Props `{ data: PricesNow | undefined; error: ApiError | null }`; der Preis jetzt steht in `data-testid="price-now"`

- [ ] **Step 1: Failing Tests schreiben**

```ts
// ui/src/lib/charts/options.test.ts
import type { LineSeriesOption, YAXisComponentOption } from 'echarts';
import { describe, expect, it } from 'vitest';
import type { PriceSlot } from '$lib/api/schemas';
import { priceForecastOption } from './options';
import type { Palette } from './palette';

const palette: Palette = {
	text: '#111',
	muted: '#666',
	grid: '#ddd',
	importPrice: '#c00',
	feedIn: '#090',
	pv: '#fa0',
	series: ['#00f', '#0a0']
};

const slot = (start: string, ct: number, feedIn: number | null = 7.3): PriceSlot => ({
	start,
	spot_net_ct: 9,
	import_net_ct: ct / 1.2,
	import_gross_ct: ct,
	feed_in_ct: feedIn,
	origin: 'primary',
	missing: []
});

const at = (minute: number) => Date.UTC(2026, 9, 9, 8, minute);

describe('Preise und Prognose', () => {
	it('zeichnet Preise als Stufen und schließt den letzten Slot ab', () => {
		const option = priceForecastOption(
			[slot('2026-10-09T08:00:00+00:00', 13.44), slot('2026-10-09T08:15:00+00:00', 15)],
			[],
			'2026-10-09T08:05:00+00:00',
			palette
		);
		const series = option.series as LineSeriesOption[];
		expect(series.map((s) => s.name)).toEqual(['Bezugspreis', 'Einspeisung']);
		expect([series[0].step, series[0].yAxisIndex]).toEqual(['end', 0]);
		expect(series[0].data).toEqual([[at(0), 13.44], [at(15), 15], [at(30), 15]]);
	});

	it('zeigt die PV-Prognose in kW auf der zweiten Achse und markiert jetzt', () => {
		const option = priceForecastOption(
			[slot('2026-10-09T08:00:00+00:00', 13.44, null)],
			[{ start: '2026-10-09T08:00:00+00:00', p50_w: 2500 }],
			'2026-10-09T08:05:00+00:00',
			palette
		);
		const series = option.series as LineSeriesOption[];
		expect(series.map((s) => s.name)).toEqual(['Bezugspreis', 'PV-Prognose']);
		expect([series[1].yAxisIndex, series[1].data]).toEqual([1, [[at(0), 2.5]]]);
		expect((option.yAxis as YAXisComponentOption[]).map((axis) => axis.name)).toEqual(['ct/kWh', 'kW']);
		expect(JSON.stringify(series[0].markLine)).toContain(String(at(5)));
	});
});
```

```ts
// ui/src/lib/dashboard/prices.test.ts
import { expect, it } from 'vitest';
import type { PriceSlot } from '$lib/api/schemas';
import { priceSummary } from './prices';

const slot = (minute: number, ct: number, extra: Partial<PriceSlot> = {}): PriceSlot => ({
	start: new Date(Date.UTC(2026, 9, 9, 8, minute)).toISOString(),
	spot_net_ct: 9,
	import_net_ct: ct / 1.2,
	import_gross_ct: ct,
	feed_in_ct: 7.3,
	origin: 'primary',
	missing: [],
	...extra
});

it('teilt die nächsten 3 h in günstig, mittel und teuer', () => {
	const next = [10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21].map((ct, i) => slot(15 * i, ct));
	const summary = priceSummary({ now: slot(0, 13.44), next_3h: next });
	expect([summary.nowCt, summary.feedInCt]).toEqual([13.44, 7.3]);
	expect(summary.next.map((n) => n.level)).toEqual([
		'low', 'low', 'low', 'low', 'mid', 'mid', 'mid', 'mid', 'high', 'high', 'high', 'high'
	]);
});

it('meldet Ersatzquelle und fehlende Tarifwerte', () => {
	const now = slot(0, 13.44, { origin: 'fallback', missing: ['grid_loss'] });
	expect(priceSummary({ now, next_3h: [] })).toMatchObject({ origin: 'fallback', incomplete: true });
});

it('ohne Preis für jetzt bleibt alles leer', () => {
	expect(priceSummary({ now: null, next_3h: [] })).toEqual({
		nowCt: null,
		feedInCt: null,
		origin: null,
		incomplete: false,
		next: []
	});
});
```

```ts
// ui/src/lib/dashboard/PriceNow.test.ts
import { render, screen } from '@testing-library/svelte';
import { expect, it } from 'vitest';
import { ApiError } from '$lib/api/errors';
import type { PriceSlot } from '$lib/api/schemas';
import PriceNow from './PriceNow.svelte';

const now: PriceSlot = {
	start: '2026-10-09T08:00:00+00:00',
	spot_net_ct: 9,
	import_net_ct: 11.2,
	import_gross_ct: 13.44,
	feed_in_ct: 7.3,
	origin: 'fallback',
	missing: ['grid_loss']
};

it('zeigt den Preis jetzt, die Ersatzquelle und fehlende Tarifwerte', () => {
	render(PriceNow, { data: { now, next_3h: [] }, error: null });
	expect(screen.getByText('13,44 ct/kWh')).toBeInTheDocument();
	expect(screen.getByText('Ersatzquelle')).toBeInTheDocument();
	expect(screen.getByText('Nicht alle Tarifbestandteile eingetragen (als 0 gerechnet)')).toBeInTheDocument();
});

it('zeigt ohne Preis einen Strich und den Fehler', () => {
	render(PriceNow, { data: { now: null, next_3h: [] }, error: new ApiError(0, 'Keine Verbindung zum Server') });
	expect(screen.getByTestId('price-now')).toHaveTextContent('–');
	expect(screen.getByText('Keine Verbindung zum Server')).toBeInTheDocument();
});
```

- [ ] **Step 2: Tests laufen lassen**

Run: `cd ui && pnpm vitest run src/lib/charts src/lib/dashboard`
Expected: FAIL in den neuen Tests – Module fehlen.

- [ ] **Step 3: Implementieren**

- `options.ts`: Zeitachse `type: 'time'`, Zeitformat in Wien über `formatTime` im `axisLabel.formatter` und Tooltip; Preisreihen mit `step: 'end'` und einem Schlusspunkt bei Beginn des letzten Slots + `SLOT_MS`; „Einspeisung“ gestrichelt; „PV-Prognose“ als Fläche (`areaStyle`, `smooth`) auf Achse 1 in kW; `markLine` auf der Bezugspreis-Reihe bei `parseIso(nowIso)` mit Beschriftung „jetzt“; `backgroundColor: 'transparent'`, Farben nur aus der `Palette`.
- `EChart.svelte`: `onMount` lädt `loadECharts()`, `init(element, null, { renderer: 'canvas' })`, `setOption(option, { notMerge: true })`; ein `$effect` setzt geänderte Optionen; `ResizeObserver` ruft `resize()`; beim Verlassen `dispose()`. Container mit `role="img"` und `aria-label={label}`.
- `palette.ts`: liest `getComputedStyle(element ?? document.documentElement)` und die Variablen aus Task 8.
- `PriceNow.svelte`: Preis jetzt groß (`formatCt`), Einspeisung klein, Kennzeichen „Ersatzquelle“ bei `fallback`, Hinweis bei `incomplete`, zwölf Balken der nächsten 3 h mit Uhrzeit und Stufe als Farbe und Text (für Screenreader „günstig/mittel/teuer“), Fehlertext aus `error.detail`.
- `+page.svelte`: Karte „Strompreis“ (`Resource` auf `api.pricesNow`, 60 s) und Karte „Preise und PV heute/morgen“ (`api.prices(null)` und `api.forecast`, je 5 min), Option als `$derived`. Die Palette wird neu gelesen, wenn `theme.dark` wechselt; „jetzt“ schaltet ein 60-s-Takt weiter.

- [ ] **Step 4: Tests und Build laufen lassen**

Run: `cd ui && pnpm test && pnpm check && pnpm build`
Expected: PASS; im Build liegt ECharts in einem eigenen Chunk unter `build/_app/immutable/chunks/`.

- [ ] **Step 5: Commit**

```bash
git add ui/src
git commit -m "feat(ui): Strompreis jetzt und Diagramm für Preise und PV-Prognose"
```

---

### Task 11: Verlauf und Tagesbilanz

**Files:**
- Create: `ui/src/lib/history/ranges.ts`, `ui/src/lib/history/balance.ts`, `ui/src/lib/history/SeriesPicker.svelte`, `ui/src/lib/history/BalanceTable.svelte`, `ui/src/routes/verlauf/+page.svelte`
- Modify: `ui/src/lib/charts/options.ts` (`historyOption`), `ui/src/lib/testing/fixtures.ts` (`daySummary(overrides)`)
- Test: `ui/src/lib/history/ranges.test.ts`, `ui/src/lib/history/balance.test.ts`, `ui/src/lib/charts/options.test.ts` (Ergänzung)

**Interfaces:**
- Consumes: `api.historyCatalog`, `api.history`, `api.ledgerDays`, `HistoryResponse`, `DaySummary` (Task 5), `todayVienna`, `addDays`, `localRange` (Task 1), `EChart`, `Palette` (Task 10), `Resource` (Task 6).
- Produces:
  - `ranges.ts`: `type RangePreset = 'heute' | 'gestern' | '7t' | '30t'`, `PRESETS: { id: RangePreset; label: string }[]` (Heute, Gestern, 7 Tage, 30 Tage), `interface DayRange { first: string; last: string }` (lokale Tage, inklusiv), `presetDays(preset: RangePreset, now: Date): DayRange`, `rangeQuery(days: DayRange): { from: string; to: string }`, `isToday(days: DayRange, now: Date): boolean`, `DEFAULT_SERIES = ['grid', 'pv', 'battery', 'house'] as const`, `MAX_SERIES = 8`
  - `options.ts`: `historyOption(data: HistoryResponse, palette: Palette): EChartsCoreOption` (eine Achse je Einheit in der Reihenfolge des ersten Auftretens; `W` als kW, sonst die Einheit selbst; Lücke, wenn zwei Punkte mehr als 1,5 Schritte auseinander liegen: Punkt `[vorher + Schritt, null]` einfügen)
  - `balance.ts`: `type BalanceColumn = 'coverage' | 'pv' | 'import' | 'export' | 'charge' | 'discharge' | 'house' | 'wallboxes' | 'cost' | 'revenue' | 'net' | 'autarky' | 'selfConsumption'`, `BALANCE_COLUMNS: { key: BalanceColumn; label: string }[]` (Abdeckung, PV, Bezug, Einspeisung, Akku geladen, Akku entladen, Haus, Wallboxen, Kosten, Erlös, Netto, Autarkie, Eigenverbrauch), `interface BalanceRow { date: string; day: string; cells: Record<BalanceColumn, string>; counters: { name: string; value: string }[] }`, `balanceRows(days: DaySummary[]): BalanceRow[]`, `counterLabel(key: string): string` (`grid.energy_import_kwh` → „Netz Bezug“, `grid.energy_export_kwh` → „Netz Einspeisung“, `pv.<id>.energy_kwh` → „PV <id>“, `load.<id>.energy_kwh` → „Last <id>“, `wallbox.<n>.total_kwh` → „Wallbox <n>“, sonst der Schlüssel)

- [ ] **Step 1: Failing Tests schreiben**

```ts
// ui/src/lib/history/ranges.test.ts
import { expect, it } from 'vitest';
import { presetDays, rangeQuery } from './ranges';

it('Heute am 25.10. umfasst 25 h', () => {
	const days = presetDays('heute', new Date('2026-10-25T10:00:00Z'));
	expect(days).toEqual({ first: '2026-10-25', last: '2026-10-25' });
	expect(rangeQuery(days)).toEqual({
		from: '2026-10-24T22:00:00.000Z',
		to: '2026-10-25T23:00:00.000Z'
	});
});

it('rechnet Gestern, 7 und 30 Tage in Wien', () => {
	const lateEvening = new Date('2026-10-09T22:30:00Z'); // 10.10., 00:30 in Wien
	expect(presetDays('gestern', lateEvening)).toEqual({ first: '2026-10-09', last: '2026-10-09' });
	expect(presetDays('7t', lateEvening)).toEqual({ first: '2026-10-04', last: '2026-10-10' });
	expect(presetDays('30t', lateEvening)).toEqual({ first: '2026-09-11', last: '2026-10-10' });
});
```

```ts
// ui/src/lib/history/balance.test.ts
import { expect, it } from 'vitest';
import { daySummary } from '$lib/testing/fixtures';
import { balanceRows } from './balance';

it('formatiert eine Tageszeile', () => {
	const [row] = balanceRows([
		daySummary({
			date: '2026-10-09',
			slots: 4,
			expected_slots: 96,
			pv_kwh: 3.2,
			wallbox_kwh: { evcs: 0, twc: 1.38 },
			revenue_eur: null,
			autarky: 0.873
		})
	]);
	expect(row.day).toBe('Fr., 09.10.');
	expect([row.cells.coverage, row.cells.pv, row.cells.wallboxes]).toEqual([
		'4 von 96',
		'3,20 kWh',
		'1,38 kWh'
	]);
	expect([row.cells.revenue, row.cells.autarky]).toEqual(['–', '87 %']);
});

it('Tagesbilanz zeigt 100 Viertelstunden am 25.10.', () => {
	const [row] = balanceRows([daySummary({ date: '2026-10-25', slots: 100, expected_slots: 100 })]);
	expect(row.cells.coverage).toBe('100 von 100');
});

it('listet die Zählerstände für den Abgleich mit VRM', () => {
	const [row] = balanceRows([
		daySummary({ counter_kwh: { 'grid.energy_import_kwh': 12.345, 'pv.huawei.energy_kwh': 3.21 } })
	]);
	expect(row.counters).toEqual([
		{ name: 'Netz Bezug', value: '12,3 kWh' },
		{ name: 'PV huawei', value: '3,21 kWh' }
	]);
});
```

Ergänzung in `ui/src/lib/charts/options.test.ts` (Import um `historyOption` erweitern):

```ts
describe('Verlauf', () => {
	const data = {
		rp: 'raw' as const,
		step_s: 60,
		series: {
			grid: { label: 'Netz', unit: 'W', points: [[0, 1000], [60_000, 2000], [300_000, 3000]] as [number, number][] },
			'soc.battery': { label: 'Akku-SOC', unit: '%', points: [[0, 55]] as [number, number][] }
		}
	};

	it('trägt jede Einheit auf eigener Achse, Leistung in kW', () => {
		const option = historyOption(data, palette);
		expect((option.yAxis as YAXisComponentOption[]).map((axis) => axis.name)).toEqual(['kW', '%']);
		const [grid, soc] = option.series as LineSeriesOption[];
		expect([grid.name, grid.yAxisIndex, soc.name, soc.yAxisIndex]).toEqual(['Netz', 0, 'Akku-SOC', 1]);
	});

	it('unterbricht die Linie bei Lücken', () => {
		const [grid] = historyOption(data, palette).series as LineSeriesOption[];
		expect(grid.data).toEqual([[0, 1], [60_000, 2], [120_000, null], [300_000, 3]]);
	});
});
```

- [ ] **Step 2: Tests laufen lassen**

Run: `cd ui && pnpm vitest run src/lib/history src/lib/charts`
Expected: FAIL – `ranges`, `balance`, `historyOption` und `daySummary` fehlen.

- [ ] **Step 3: Implementieren**

- `ranges.ts`, `balance.ts` und `historyOption` nach den Tests. `wallboxes` ist die Summe von `wallbox_kwh`, `coverage` „<slots> von <expected_slots>“; Geldbeträge mit `formatEur`, Anteile mit `formatRatio`.
- `SeriesPicker.svelte`: Kontrollkästchen aus dem Katalog, höchstens `MAX_SERIES` (danach gesperrt mit Hinweis).
- `BalanceTable.svelte`: Tabelle in einem eigenen Scroll-Container, Zählerstände je Tag aufklappbar (`<details>`), über der Tabelle der Hinweis „Zählerstände für den Abgleich mit VRM“.
- `verlauf/+page.svelte`: Zeitraum-Knöpfe und zwei Datumsfelder (erster und letzter Tag, höchstens 400 Tage, Fehlermeldung bei vertauschter Reihenfolge). Diagramm (`historyOption`, Fehler wie „InfluxDB nicht erreichbar“ als `Notice`) und darunter die Tagesbilanz (`api.ledgerDays`). Für „Heute“ laufen beide im 60-s-Takt, sonst einmal. Die Wahl der Reihen steht in der URL (`?reihen=grid,pv`), damit Links sie behalten.

- [ ] **Step 4: Tests laufen lassen**

Run: `cd ui && pnpm test && pnpm check`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ui/src
git commit -m "feat(ui): Verlauf mit Diagramm und Tagesbilanz"
```

---

### Task 12: Verbraucher

**Files:**
- Create: `ui/src/lib/consumers/form.ts`, `ui/src/lib/consumers/ConsumerTree.svelte`, `ui/src/lib/consumers/ConsumerForm.svelte`, `ui/src/routes/verbraucher/+page.svelte`
- Test: `ui/src/lib/consumers/form.test.ts`, `ui/src/lib/consumers/ConsumerTree.test.ts`

**Interfaces:**
- Consumes: `consumerRows`, `ConsumerRow` (Task 9); `api.consumers`, `api.consumerCandidates`, `api.createConsumer`, `api.updateConsumer`, `api.deleteConsumer`, `Consumer`, `ConsumerInput`, `Candidates` (Task 5); `Dialog`, `Notice` (Task 8); `hasRole` (Task 7).
- Produces:
  - `form.ts`: `interface ConsumerDraft { name: string; group: string; parentId: string; color: string; sourceKind: 'core' | 'ha'; powerRef: string; powerUnit: '' | 'W' | 'kW'; sort: string }`, `type DraftErrors = Partial<Record<keyof ConsumerDraft, string>>`, `emptyDraft(): ConsumerDraft` (Farbe `#4f8cff`, Quelle `core`, Reihenfolge `0`, sonst leer), `draftFromConsumer(consumer: Consumer): ConsumerDraft`, `draftToInput(draft: ConsumerDraft): { input: ConsumerInput } | { errors: DraftErrors }`, `parentOptions(consumers: Consumer[], editingId: number | null): { id: number; name: string }[]` (ohne den bearbeiteten Verbraucher und seine Nachkommen)
  - `ConsumerTree.svelte`: Props `{ rows: ConsumerRow[]; admin: boolean; onEdit: (id: number) => void; onDelete: (id: number) => void }`
  - `ConsumerForm.svelte`: Props `{ draft: ConsumerDraft` (`$bindable`), `consumers: Consumer[]; candidates: Candidates | undefined; editingId: number | null; onSave: (input: ConsumerInput) => Promise<void>; onCancel: () => void }`

- [ ] **Step 1: Failing Tests schreiben**

```ts
// ui/src/lib/consumers/form.test.ts
import { expect, it } from 'vitest';
import type { Consumer } from '$lib/api/schemas';
import { draftToInput, emptyDraft, parentOptions } from './form';

const consumer = (id: number, name: string, parent: number | null): Consumer => ({
	id,
	name,
	group: null,
	parent_id: parent,
	color: '#4f8cff',
	source_kind: 'core',
	power_ref: `load.k${id}.power_w`,
	power_unit: null,
	sort: 0
});

it('übernimmt gültige Angaben', () => {
	const draft = { ...emptyDraft(), name: ' Küche ', sourceKind: 'ha' as const, powerRef: 'sensor.kueche_power', powerUnit: 'W' as const };
	expect(draftToInput(draft)).toEqual({
		input: {
			name: 'Küche',
			group: null,
			parent_id: null,
			color: '#4f8cff',
			source_kind: 'ha',
			power_ref: 'sensor.kueche_power',
			power_unit: 'W',
			sort: 0
		}
	});
});

it('prüft core-Signale wie der Server', () => {
	expect(draftToInput({ ...emptyDraft(), name: 'OG', powerRef: 'load.og' })).toEqual({
		errors: { powerRef: 'core-Signal muss auf .power_w enden, z. B. load.obergeschoss.power_w' }
	});
});

it('verlangt bei HA-Entitäten Format und Einheit', () => {
	expect(draftToInput({ ...emptyDraft(), name: 'K', sourceKind: 'ha', powerRef: 'kueche' })).toEqual({
		errors: {
			powerRef: 'HA-Entität als domain.objekt angeben, z. B. sensor.kueche',
			powerUnit: 'Einheit wählen (W oder kW)'
		}
	});
});

it('meldet Name, Farbe und Reihenfolge', () => {
	const draft = { ...emptyDraft(), name: ' ', color: 'blau', sort: '1,5', powerRef: 'load.og.power_w' };
	expect(draftToInput(draft)).toEqual({
		errors: {
			name: 'Name fehlt',
			color: 'Farbe als #rrggbb angeben',
			sort: 'ganze Zahl zwischen -1000000 und 1000000'
		}
	});
});

it('bietet keine Elternelemente an, die einen Kreis ergäben', () => {
	const consumers = [consumer(1, 'OG', null), consumer(2, 'Büro', 1), consumer(3, 'Schreibtisch', 2), consumer(4, 'Küche', null)];
	expect(parentOptions(consumers, 1).map((option) => option.id)).toEqual([4]);
	expect(parentOptions(consumers, null).map((option) => option.id)).toEqual([1, 2, 3, 4]);
});
```

```ts
// ui/src/lib/consumers/ConsumerTree.test.ts
import { render, screen } from '@testing-library/svelte';
import { expect, it, vi } from 'vitest';
import type { TreeNode } from '$lib/api/schemas';
import ConsumerTree from './ConsumerTree.svelte';
import { consumerRows } from './tree';

const leaf = (id: number, name: string, power: number): TreeNode => ({
	id, name, color: '#4f8cff', power_w: power, other_w: null, mismatch: false, children: []
});
const tree = (mismatch = false): TreeNode => ({
	id: null,
	name: 'Haus',
	color: null,
	power_w: mismatch ? 100 : 1800,
	other_w: mismatch ? 0 : 1680,
	mismatch,
	children: [leaf(1, 'Küche', 120)]
});

it('zeigt „Sonstiges“ und Admins die Knöpfe zum Bearbeiten', () => {
	render(ConsumerTree, { rows: consumerRows(tree()), admin: true, onEdit: vi.fn(), onDelete: vi.fn() });
	expect(screen.getByText('Sonstiges')).toBeInTheDocument();
	expect(screen.getAllByRole('button', { name: /Küche bearbeiten/ })).toHaveLength(1);
});

it('zeigt Lesenden keine Knöpfe', () => {
	render(ConsumerTree, { rows: consumerRows(tree()), admin: false, onEdit: vi.fn(), onDelete: vi.fn() });
	expect(screen.queryAllByRole('button')).toHaveLength(0);
});

it('Abweichung wird angezeigt', () => {
	render(ConsumerTree, { rows: consumerRows(tree(true)), admin: false, onEdit: vi.fn(), onDelete: vi.fn() });
	expect(screen.getByText('Unterverbraucher messen mehr als der Elternverbraucher')).toBeInTheDocument();
});
```

- [ ] **Step 2: Tests laufen lassen**

Run: `cd ui && pnpm vitest run src/lib/consumers`
Expected: FAIL in `form.test.ts` und `ConsumerTree.test.ts`.

- [ ] **Step 3: Implementieren**

- `form.ts` mit denselben Regeln wie der Server (Anhang A, Verbraucher): Name 1–60 Zeichen (getrimmt), Gruppe höchstens 60, Farbe `^#[0-9a-fA-F]{6}$`, core-Signal `^[a-z0-9_]+(\.[a-z0-9_]+)*\.power_w$` ohne Einheit, HA-Entität `^[a-z_]+\.[a-z0-9_]+$` mit Einheit, Reihenfolge ganze Zahl in [-1000000, 1000000]; leere Gruppe und leeres Elternelement werden `null`.
- `ConsumerTree.svelte`: Zeilen mit Einrückung je Tiefe, Farbpunkt, `formatPower`; „Sonstiges“ kursiv; bei `mismatch` der Hinweis im Text; für Admins je Verbraucher die Knöpfe „<Name> bearbeiten“ und „<Name> löschen“ (Wurzel und „Sonstiges“ ohne Knöpfe).
- `ConsumerForm.svelte`: Quelle wählen (core-Signal oder HA-Entität); Vorschläge aus `candidates` (während des Ladens „Suche Signale …“, das kann wegen InfluxDB dauern), freie Eingabe bleibt möglich; Elternelement aus `parentOptions`; Fehler aus `draftToInput` am Feld, Serverfehler (`detailText`) über dem Formular.
- `verbraucher/+page.svelte`: Baum (`Resource` auf `api.consumers`, 15 s). Für Admins „Verbraucher anlegen“ und Bearbeiten im `Dialog` (Kandidaten erst beim Öffnen laden), Löschen mit Bestätigung; 409 „Verbraucher hat Unterverbraucher“ und 422 als Text; danach `refresh()`.

- [ ] **Step 4: Tests laufen lassen**

Run: `cd ui && pnpm test && pnpm check`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ui/src
git commit -m "feat(ui): Verbraucher mit Baum, Sonstiges und Verwaltung"
```

---

### Task 13: System

**Files:**
- Create: `ui/src/lib/system/view.ts`, `ui/src/lib/system/SignalTable.svelte`, `ui/src/routes/system/+page.svelte`
- Test: `ui/src/lib/system/view.test.ts`, `ui/src/lib/system/SignalTable.test.ts`

**Interfaces:**
- Consumes: `api.system`, `api.prices`, `api.forecast`, `api.refreshPrices`, `api.health` (Task 5), `live` (Task 6), `Resource` (Task 6), `StatusDot`, `Card`, `Notice` (Task 8), `formatDateTime`, `parseIso` (Task 1), `coreState`, `reading` (Task 9).
- Produces:
  - `view.ts`: `componentLabel(name: string): string` (core „core“, prices „Preise“, forecast „PV-Prognose“, ledger „Abrechnung“, influx „InfluxDB“, ha „Home Assistant“, sonst der Name), `selfcheckLabel(id: string): string` (phases „Phasen (vebus)“, ess_mode „ESS-Modus“, batterylife „BatteryLife“, dess „Dynamic ESS“, schedules „Victron-Ladefenster“, min_soc „Min-SOC“, fresh_data „Datenfrische“, `evcs_mode.<wb>` „EVCS-Modus <wb>“, sonst die ID), `signalValue(value: Reading['v']): string` (Zahl de-AT mit höchstens 3 Nachkommastellen, `true` „ja“, `false` „nein“, `null` „–“, Text unverändert), `interface SignalRow { name: string; value: string; quality: Reading['q']; ageS: number | null }`, `signalRows(state: CoreState | null | undefined, nowMs: number, filter: string): SignalRow[]` (nach Name sortiert, Filter als Teilstring ohne Groß/klein), `vatText(status: PriceStatus): string`
  - `SignalTable.svelte`: Props `{ rows: SignalRow[] }`

- [ ] **Step 1: Failing Tests schreiben**

```ts
// ui/src/lib/system/view.test.ts
import { expect, it } from 'vitest';
import type { PriceStatus } from '$lib/api/schemas';
import { coreState, reading } from '$lib/testing/fixtures';
import { selfcheckLabel, signalRows, vatText } from './view';

it('benennt Selbstprüfungen deutsch', () => {
	expect(['dess', 'evcs_mode.twc', 'neu'].map(selfcheckLabel)).toEqual([
		'Dynamic ESS',
		'EVCS-Modus twc',
		'neu'
	]);
});

it('listet Signale mit Wert, Qualität und Alter', () => {
	const state = {
		...coreState(),
		signals: {
			'grid.power_w': reading(512.25),
			'vebus.mode': reading('ON', 'stale'),
			'vehicle.tesla.plugged': { v: true, ts: '2026-10-09T07:59:30+00:00', q: 'ok' as const },
			'battery.soc_pct': reading(null, 'invalid')
		}
	};
	const now = Date.UTC(2026, 9, 9, 8, 0, 10);
	expect(signalRows(state, now, '')).toEqual([
		{ name: 'battery.soc_pct', value: '–', quality: 'invalid', ageS: 10 },
		{ name: 'grid.power_w', value: '512,25', quality: 'ok', ageS: 10 },
		{ name: 'vebus.mode', value: 'ON', quality: 'stale', ageS: 10 },
		{ name: 'vehicle.tesla.plugged', value: 'ja', quality: 'ok', ageS: 40 }
	]);
	expect(signalRows(state, now, 'GRID').map((row) => row.name)).toEqual(['grid.power_w']);
});

it('beschreibt die Brutto/Netto-Erkennung', () => {
	const status = (part: Partial<PriceStatus>): PriceStatus => ({
		last_attempt: null,
		last_success: null,
		vat_mode: null,
		vat_detection: null,
		days: [],
		errors: [],
		...part
	});
	expect(vatText(status({ vat_mode: 'gross', vat_detection: { result: 'gross', ratio: 1.2, slots: 96 } }))).toBe(
		'brutto (erkannt, Verhältnis 1,200 aus 96 Slots)'
	);
	expect(vatText(status({ vat_mode: 'gross', vat_detection: { result: null, ratio: 1.1, slots: 40 } }))).toBe(
		'brutto (Verhältnis 1,100 aus 40 Slots, nicht eindeutig)'
	);
	expect(vatText(status({}))).toBe('noch nicht bestimmt');
});
```

`reading(v, q)` setzt `ts` auf `2026-10-09T08:00:00+00:00` (siehe `testing/fixtures.ts`, Task 9).

```ts
// ui/src/lib/system/SignalTable.test.ts
import { render, screen } from '@testing-library/svelte';
import { expect, it } from 'vitest';
import SignalTable from './SignalTable.svelte';

it('markiert veraltete und ungültige Werte', () => {
	render(SignalTable, {
		rows: [
			{ name: 'vebus.mode', value: 'ON', quality: 'stale', ageS: 40 },
			{ name: 'battery.soc_pct', value: '–', quality: 'invalid', ageS: null }
		]
	});
	expect(screen.getByText('veraltet')).toBeInTheDocument();
	expect(screen.getByText('ungültig')).toBeInTheDocument();
	expect(screen.getByText('vor 40 s')).toBeInTheDocument();
});
```

- [ ] **Step 2: Tests laufen lassen**

Run: `cd ui && pnpm vitest run src/lib/system`
Expected: FAIL – Module fehlen.

- [ ] **Step 3: Implementieren**

- `view.ts` nach den Tests; `vatText`: `vat_mode` als „netto“/„brutto“; Erkennung gleich `vat_mode` → „(erkannt, Verhältnis x aus n Slots)“; mit Verhältnis, aber ohne Ergebnis → „(Verhältnis x aus n Slots, nicht eindeutig)“; sonst nur das Wort. Alter aus `parseIso(ts)`.
- `SignalTable.svelte`: Tabelle (Name, Wert, Qualität, Alter „vor N s“, ab 120 s „vor N min“) im eigenen Scroll-Container; `ok` ohne Kennzeichen.
- `system/+page.svelte`, Abschnitte:
  1. Komponenten: `StatusDot`, Name, Meldung, „seit“.
  2. Hinweise (`warnings`).
  3. core: Verbindung, Betriebsart (`OBSERVE` → „Beobachten (nur lesend)“), Versionen von core und app (`api.health`), Zykluszeit p95, Adapter (Tabelle: Name, verbunden, letzter Erfolg, letzter Fehler, Fehlerzahl), Selbstprüfung (`StatusDot` je Status `ok`/`warn`/`fail`/`unknown`), Alarme.
  4. Preise (`api.prices(null)`, 5 min): letzter Versuch und Erfolg, `vatText`, Tage (Datum, Herkunft „smartENERGY“/„Ersatzquelle“/„keine Preise“, Befunde), Fehler. Für Admins „Preise jetzt abrufen“ (`api.refreshPrices`, Knopf bis zur Antwort gesperrt, Hinweis „kann bis zu 30 s dauern“).
  5. PV-Prognose (`api.forecast`, 5 min): Status, Ausgabezeit, kWh für heute und die zwei Folgetage.
  6. Signale: `SignalTable` aus `live.state` mit Filterfeld „Signal suchen“; das Alter zählt ein 1-s-Takt weiter.
  
  `api.system` läuft im 15-s-Takt.

- [ ] **Step 4: Tests laufen lassen**

Run: `cd ui && pnpm test && pnpm check`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ui/src
git commit -m "feat(ui): System-Seite mit Komponenten, core, Preisen, Prognose und Signalen"
```

---

### Task 14: Einstellungen

**Files:**
- Create: `ui/src/lib/settings/model.ts`, `ui/src/lib/settings/TariffView.svelte`, `ui/src/lib/settings/SettingsEditor.svelte`, `ui/src/lib/settings/LimitsView.svelte`, `ui/src/lib/settings/ImportExport.svelte`, `ui/src/lib/settings/RepriceForm.svelte`, `ui/src/routes/einstellungen/+page.svelte`
- Modify: `ui/src/lib/testing/fixtures.ts` (`settingsCurrent()`), `docs/betrieb.md` (Abschnitt 8: Bedienung im UI, curl nur noch als Alternative)
- Test: `ui/src/lib/settings/model.test.ts`, `ui/src/lib/settings/SettingsEditor.test.ts`, `ui/src/lib/settings/RepriceForm.test.ts`

**Interfaces:**
- Consumes: `api.settings`, `api.saveSettings`, `api.settingsVersions`, `api.importSettings`, `api.limits`, `api.reprice`, `EXPORT_SETTINGS_URL`, `fieldErrors`, `detailText`, `ApiError` (Task 5), `hasRole` (Task 7), `Dialog`, `Notice` (Task 8), `todayVienna`, `addDays` (Task 1).
- Produces:
  - `model.ts`: `interface SettingsDraft { values: Record<string, string>; feedIn: { month: string; ct: string }[]; prices: PriceSettings; comment: string }`, `type DraftResult = { settings: RuntimeSettings; tariffChanged: boolean } | { errors: Record<string, string> }`, `parseDecimal(text: string): number | null | 'invalid'`, `decimalText(value: number | null): string`, `draftFrom(settings: RuntimeSettings): SettingsDraft` (Werte nur für Bestandteile mit `source: 'fixed'`), `applyDraft(settings: RuntimeSettings, draft: SettingsDraft): DraftResult` (Fehlerschlüssel `values.<id>`, `feedIn.<index>.month`, `feedIn.<index>.ct`), `describeWindow(window: TimeWindow): string`, `validityText(component: PriceComponent): string`, `editorFieldErrors(detail: unknown, settings: RuntimeSettings): Record<string, string>` (422-Pfade auf dieselben Schlüssel; `tariff.components.<i>.value_ct` → `values.<id>`), `defaultRepriceRange(now: Date): { first: string; last: string }` (Monatsbeginn bis heute in Wien)
  - `SettingsEditor.svelte`: Props `{ current: SettingsCurrent; save: (baseVersion: number, settings: RuntimeSettings, comment: string | null) => Promise<SettingsCurrent>; onSaved: (result: SettingsCurrent, tariffChanged: boolean) => void; onReload: () => void }`; Feld je fester Bestandteil mit der Beschriftung „<name> (ct/kWh netto)“
  - `RepriceForm.svelte`: Props `{ initial: { first: string; last: string }; reprice: (first: string, last: string) => Promise<{ repriced: number }> }`

- [ ] **Step 1: Failing Tests schreiben**

```ts
// ui/src/lib/settings/model.test.ts
import { expect, it } from 'vitest';
import error422 from '$lib/api/contract/error-422.json';
import { settingsCurrent } from '$lib/testing/fixtures';
import {
	applyDraft,
	defaultRepriceRange,
	describeWindow,
	draftFrom,
	editorFieldErrors,
	parseDecimal,
	validityText
} from './model';

const settings = () => settingsCurrent().settings;

it('liest deutsche Dezimalzahlen', () => {
	expect(parseDecimal('7,3')).toBe(7.3);
	expect(parseDecimal(' 8.11 ')).toBe(8.11);
	expect(parseDecimal('')).toBeNull();
	expect(parseDecimal('abc')).toBe('invalid');
	expect(parseDecimal('1,2,3')).toBe('invalid');
});

it('ändert ohne Eingaben nichts', () => {
	const current = settings();
	expect(applyDraft(current, draftFrom(current))).toEqual({ settings: current, tariffChanged: false });
});

it('übernimmt einen Tarifwert mit Komma', () => {
	const current = settings();
	const draft = draftFrom(current);
	draft.values.grid_loss = '1,23';
	const result = applyDraft(current, draft);
	if (!('settings' in result)) throw new Error(JSON.stringify(result.errors));
	expect(result.tariffChanged).toBe(true);
	expect(result.settings.tariff.components.find((c) => c.id === 'grid_loss')?.value_ct).toBe(1.23);
});

it('meldet ungültige Werte und doppelte Monate', () => {
	const current = settings();
	const draft = draftFrom(current);
	draft.values.grid_loss = 'viel';
	draft.feedIn = [
		{ month: '2026-09', ct: '7,3' },
		{ month: '2026-09', ct: '8' },
		{ month: '2026-10', ct: '-1' }
	];
	expect(applyDraft(current, draft)).toEqual({
		errors: {
			'values.grid_loss': 'Zahl, z. B. 8,11',
			'feedIn.1.month': 'Monat doppelt',
			'feedIn.2.ct': 'Wert ab 0'
		}
	});
});

it('beschreibt Zeitfenster und Gültigkeit', () => {
	const gridUsage = settings().tariff.components.find((c) => c.id === 'grid_usage');
	if (!gridUsage) throw new Error('grid_usage fehlt');
	expect(describeWindow(gridUsage.windows[0])).toBe('Apr–Sep, Mo–So, 10:00–16:00: × 0,8');
	expect(validityText({ ...gridUsage, valid_from: null, valid_until: null })).toBe('immer');
	expect(validityText({ ...gridUsage, valid_from: '2027-01-01', valid_until: null })).toBe('ab 01.01.2027');
	expect(validityText({ ...gridUsage, valid_from: null, valid_until: '2026-12-31' })).toBe('bis 31.12.2026');
});

it('ordnet Serverfehler dem Feld zu', () => {
	expect(editorFieldErrors(error422.detail, settings())).toEqual({ 'values.grid_usage': 'Zahl erwartet' });
});

it('schlägt die Neubewertung ab Monatsbeginn vor', () => {
	expect(defaultRepriceRange(new Date('2026-10-09T08:00:00Z'))).toEqual({
		first: '2026-10-01',
		last: '2026-10-09'
	});
});
```

```ts
// ui/src/lib/settings/SettingsEditor.test.ts
import { fireEvent, render, screen } from '@testing-library/svelte';
import { expect, it, vi } from 'vitest';
import { ApiError } from '$lib/api/errors';
import error422 from '$lib/api/contract/error-422.json';
import { settingsCurrent } from '$lib/testing/fixtures';
import SettingsEditor from './SettingsEditor.svelte';

const LOSS = 'Netzverlustentgelt (ct/kWh netto)';

it('Konflikt behält die Eingaben und bietet Neuladen an', async () => {
	const save = vi
		.fn()
		.mockRejectedValueOnce(new ApiError(409, 'Die Einstellungen wurden inzwischen geändert (aktuell Version 7).'));
	render(SettingsEditor, { current: settingsCurrent(), save, onSaved: vi.fn(), onReload: vi.fn() });
	await fireEvent.input(screen.getByLabelText(LOSS), { target: { value: '1,23' } });
	await fireEvent.click(screen.getByRole('button', { name: 'Speichern' }));
	expect(
		await screen.findByText('Die Einstellungen wurden inzwischen geändert (aktuell Version 7).')
	).toBeInTheDocument();
	expect(screen.getByLabelText(LOSS)).toHaveValue('1,23');
	expect(screen.getByRole('button', { name: 'Neu laden (Eingaben verwerfen)' })).toBeInTheDocument();
});

it('Serverfehler erscheinen am Feld', async () => {
	const save = vi.fn().mockRejectedValueOnce(new ApiError(422, 'x', { detail: error422.detail }));
	render(SettingsEditor, { current: settingsCurrent(), save, onSaved: vi.fn(), onReload: vi.fn() });
	await fireEvent.input(screen.getByLabelText(LOSS), { target: { value: '1,23' } });
	await fireEvent.click(screen.getByRole('button', { name: 'Speichern' }));
	expect(await screen.findByText('Zahl erwartet')).toBeInTheDocument();
});

it('meldet nach dem Speichern, ob sich Tarifwerte geändert haben', async () => {
	const current = settingsCurrent();
	const saved = { ...current, version: current.version + 1 };
	const onSaved = vi.fn();
	render(SettingsEditor, { current, save: vi.fn().mockResolvedValueOnce(saved), onSaved, onReload: vi.fn() });
	await fireEvent.input(screen.getByLabelText(LOSS), { target: { value: '1,23' } });
	await fireEvent.click(screen.getByRole('button', { name: 'Speichern' }));
	await vi.waitFor(() => expect(onSaved).toHaveBeenCalledWith(saved, true));
});
```

```ts
// ui/src/lib/settings/RepriceForm.test.ts
import { fireEvent, render, screen } from '@testing-library/svelte';
import { expect, it, vi } from 'vitest';
import RepriceForm from './RepriceForm.svelte';

it('bewertet den gewählten Zeitraum neu', async () => {
	const reprice = vi.fn().mockResolvedValueOnce({ repriced: 2976 });
	render(RepriceForm, { initial: { first: '2026-10-01', last: '2026-10-09' }, reprice });
	await fireEvent.click(screen.getByRole('button', { name: 'Neu bewerten' }));
	expect(reprice).toHaveBeenCalledWith('2026-10-01', '2026-10-09');
	expect(await screen.findByText('2976 Viertelstunden neu bewertet.')).toBeInTheDocument();
});
```

- [ ] **Step 2: Tests laufen lassen**

Run: `cd ui && pnpm vitest run src/lib/settings`
Expected: FAIL – Module und Komponenten fehlen.

- [ ] **Step 3: Implementieren**

- `model.ts` nach den Tests. Monatskürzel `Jän, Feb, Mär, Apr, Mai, Jun, Jul, Aug, Sep, Okt, Nov, Dez`, zusammenhängende Monate als Spanne, alle zwölf als „ganzjährig“; Wochentage `Mo … So` ebenso (alle sieben → „Mo–So“); Uhrzeiten ohne Sekunden, `00:00:00` als Ende → „24:00“; Faktor als „× 0,8“, fester Wert als „8,11 ct“. Die Werte kommen netto in ct/kWh; leeres Feld = kein Wert (`null`). Der Kommentar wird getrimmt, leer → `null`.
- `SettingsEditor.svelte`: Felder für die festen Bestandteile, OeMAG-Monate (`<input type="month">` und Wert; Zeilen hinzufügen und entfernen), Brutto/Netto-Modus („automatisch“, „netto“, „brutto“), Ersatzeinstellung, Referenzquelle („Energy-Charts“, „aWATTar“), Kommentar. Erst Prüfung mit `applyDraft` (Fehler am Feld), dann `save(current.version, …)`. Bei 409: Meldung, Eingaben bleiben, Knopf „Neu laden (Eingaben verwerfen)“ ruft `onReload`. Bei 422: `editorFieldErrors` am Feld, der Rest als `detailText` über dem Formular.
- `RepriceForm.svelte`: zwei Datumsfelder, Knopf „Neu bewerten“, Ergebnis „<n> Viertelstunden neu bewertet.“
- `TariffView.svelte`: Tabelle der Bestandteile (Name, Quelle „Börse“/„fest“, Wert netto oder „fehlt“, USt ja/nein, `validityText`, Zeitfenster mit `describeWindow`), USt-Satz und Vergleichspreis („für die Ersparnis“).
- `LimitsView.svelte`: harte Grenzen aus `api.limits` mit dem Hinweis „nur in config.yaml änderbar“.
- `ImportExport.svelte`: Link „Exportieren (YAML)“ auf `EXPORT_SETTINGS_URL` mit `download`; Import über eine Datei (`.yaml`, `.yml`), Text an `api.importSettings`, Ergebnis „Version N übernommen“ oder Fehlertext mit Zeilenumbrüchen (`white-space: pre-line`).
- `einstellungen/+page.svelte`: Warnungen aus `current.warnings`; Abschnitte Tarif, Einspeisung (OeMAG), Preise, PV-Modell, Harte Grenzen für alle. Für Admins zusätzlich „Bearbeiten“ (`SettingsEditor`), nach dem Speichern neu laden und bei geänderten Tarifwerten `RepriceForm` mit `defaultRepriceRange(new Date())`; außerdem Versionen (`api.settingsVersions`: Version, Zeit, wer, Quelle, Kommentar) und `ImportExport`.
- `docs/betrieb.md` Abschnitt 8: Tarifwerte, OeMAG-Werte, Neubewertung und Import/Export im UI unter „Einstellungen“ beschreiben; die curl-Beispiele bleiben als Alternative für Skripte.

- [ ] **Step 4: Tests laufen lassen**

Run: `cd ui && pnpm test && pnpm check`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ui/src docs/betrieb.md
git commit -m "feat(ui): Einstellungen ansehen, Tarif und OeMAG bearbeiten, Abrechnung neu bewerten"
```

---

### Task 15: Konto

**Files:**
- Create: `ui/src/lib/account/qr.ts`, `ui/src/lib/account/PasswordForm.svelte`, `ui/src/lib/account/TotpPanel.svelte`, `ui/src/routes/konto/+page.svelte`
- Test: `ui/src/lib/account/qr.test.ts`, `ui/src/lib/account/PasswordForm.test.ts`, `ui/src/lib/account/TotpPanel.test.ts`

**Interfaces:**
- Consumes: `api.changePassword`, `api.totpSetup`, `api.totpEnable`, `api.totpDisable`, `api.logout`, `TotpSetup`, `ApiError` (Task 5), `ROLE_LABELS` (Task 7), `Notice` (Task 8); `uqr` (`renderSVG`).
- Produces:
  - `qr.ts`: `qrDataUrl(text: string): string` (`data:image/svg+xml;utf8,` + kodiertes SVG), `groupSecret(secret: string): string`
  - `PasswordForm.svelte`: Props `{ change: (oldPassword: string, newPassword: string) => Promise<void> }`
  - `TotpPanel.svelte`: Props `{ enabled: boolean; setup: (password: string) => Promise<TotpSetup>; enable: (code: string) => Promise<void>; disable: (password: string) => Promise<void>; onChanged: () => void }`

- [ ] **Step 1: Failing Tests schreiben**

```ts
// ui/src/lib/account/qr.test.ts
import { expect, it } from 'vitest';
import { groupSecret, qrDataUrl } from './qr';

const PREFIX = 'data:image/svg+xml;utf8,';

it('erzeugt den QR-Code als SVG-Daten-URL', () => {
	const url = qrDataUrl('otpauth://totp/BindaEMS:gast?secret=JBSWY3DPEHPK3PXP&issuer=BindaEMS');
	expect(url.startsWith(PREFIX)).toBe(true);
	expect(decodeURIComponent(url.slice(PREFIX.length))).toContain('<svg');
});

it('gruppiert das Geheimnis in Vierergruppen', () => {
	expect(groupSecret('JBSWY3DPEHPK3PXP')).toBe('JBSW Y3DP EHPK 3PXP');
});
```

```ts
// ui/src/lib/account/PasswordForm.test.ts
import { fireEvent, render, screen } from '@testing-library/svelte';
import { expect, it, vi } from 'vitest';
import { ApiError } from '$lib/api/errors';
import PasswordForm from './PasswordForm.svelte';

async function fill(old: string, next: string, repeat: string) {
	await fireEvent.input(screen.getByLabelText('Altes Passwort'), { target: { value: old } });
	await fireEvent.input(screen.getByLabelText('Neues Passwort'), { target: { value: next } });
	await fireEvent.input(screen.getByLabelText('Neues Passwort wiederholen'), { target: { value: repeat } });
	await fireEvent.click(screen.getByRole('button', { name: 'Passwort ändern' }));
}

it('prüft Länge und Wiederholung vor dem Senden', async () => {
	const change = vi.fn();
	render(PasswordForm, { change });
	await fill('alt-passwort-1', 'kurz', 'kurz');
	expect(screen.getByText('Mindestens 10 Zeichen')).toBeInTheDocument();
	await fill('alt-passwort-1', 'neues-passwort-1', 'neues-passwort-2');
	expect(screen.getByText('Passwörter stimmen nicht überein')).toBeInTheDocument();
	expect(change).not.toHaveBeenCalled();
});

it('meldet ein falsches altes Passwort und bestätigt die Änderung', async () => {
	const change = vi
		.fn()
		.mockRejectedValueOnce(new ApiError(400, 'Altes Passwort falsch'))
		.mockResolvedValueOnce(undefined);
	render(PasswordForm, { change });
	await fill('falsch-falsch', 'neues-passwort-1', 'neues-passwort-1');
	expect(await screen.findByText('Altes Passwort falsch')).toBeInTheDocument();
	await fill('alt-passwort-1', 'neues-passwort-1', 'neues-passwort-1');
	expect(
		await screen.findByText('Passwort geändert. Andere Sitzungen wurden abgemeldet.')
	).toBeInTheDocument();
});
```

```ts
// ui/src/lib/account/TotpPanel.test.ts
import { fireEvent, render, screen } from '@testing-library/svelte';
import { expect, it, vi } from 'vitest';
import { ApiError } from '$lib/api/errors';
import TotpPanel from './TotpPanel.svelte';

it('richtet TOTP mit Passwort, QR-Code und Code ein', async () => {
	const setup = vi.fn().mockResolvedValueOnce({
		secret: 'JBSWY3DPEHPK3PXP',
		uri: 'otpauth://totp/BindaEMS:gast?secret=JBSWY3DPEHPK3PXP&issuer=BindaEMS'
	});
	const enable = vi.fn().mockResolvedValueOnce(undefined);
	const onChanged = vi.fn();
	render(TotpPanel, { enabled: false, setup, enable, disable: vi.fn(), onChanged });
	await fireEvent.click(screen.getByRole('button', { name: 'Einrichten' }));
	await fireEvent.input(screen.getByLabelText('Passwort'), { target: { value: 'demo-passwort-1' } });
	await fireEvent.click(screen.getByRole('button', { name: 'Weiter' }));
	expect(await screen.findByAltText('QR-Code für die Authenticator-App')).toBeInTheDocument();
	expect(screen.getByText('JBSW Y3DP EHPK 3PXP')).toBeInTheDocument();
	await fireEvent.input(screen.getByLabelText('Bestätigungscode'), { target: { value: '123456' } });
	await fireEvent.click(screen.getByRole('button', { name: 'Aktivieren' }));
	expect(setup).toHaveBeenCalledWith('demo-passwort-1');
	expect(enable).toHaveBeenCalledWith('123456');
	expect(onChanged).toHaveBeenCalled();
});

it('verlangt zum Abschalten das Passwort', async () => {
	const disable = vi.fn().mockRejectedValueOnce(new ApiError(400, 'Passwort falsch'));
	render(TotpPanel, { enabled: true, setup: vi.fn(), enable: vi.fn(), disable, onChanged: vi.fn() });
	await fireEvent.click(screen.getByRole('button', { name: 'Abschalten' }));
	await fireEvent.input(screen.getByLabelText('Passwort'), { target: { value: 'falsch-falsch' } });
	await fireEvent.click(screen.getByRole('button', { name: 'Zwei-Faktor-Anmeldung abschalten' }));
	expect(await screen.findByText('Passwort falsch')).toBeInTheDocument();
});
```

- [ ] **Step 2: Tests laufen lassen**

Run: `cd ui && pnpm vitest run src/lib/account`
Expected: FAIL – Module und Komponenten fehlen.

- [ ] **Step 3: Implementieren**

- `qr.ts`: `renderSVG` aus `uqr` (Fehlerkorrektur M), Ergebnis mit `encodeURIComponent`.
- `PasswordForm.svelte`: drei Felder (`autocomplete` `current-password` bzw. `new-password`), Prüfung vor dem Senden, Fehler aus `ApiError.detail` (auch die Sperre mit 429), Erfolgsmeldung, danach Felder leeren.
- `TotpPanel.svelte`: Zustand aus: „Einrichten“ → Passwort → „Weiter“ (`setup`) → QR-Code (`<img alt="QR-Code für die Authenticator-App">`), gruppiertes Geheimnis zum Abtippen, Codefeld → „Aktivieren“ (`enable`, dann `onChanged`). Zustand an: „Abschalten“ → Passwort → „Zwei-Faktor-Anmeldung abschalten“. 409 („bereits aktiv“) und 429 als Text. Das Geheimnis verlässt die Komponente nicht (kein Speicher, kein Log).
- `konto/+page.svelte`: Benutzername, Rolle (`ROLE_LABELS`), TOTP-Status; für Admins ohne TOTP der Hinweis „Für Admins empfohlen“. `PasswordForm`, `TotpPanel` (nach Änderung `invalidateAll()`), Knopf „Abmelden“.

- [ ] **Step 4: Tests laufen lassen**

Run: `cd ui && pnpm test && pnpm check`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ui/src
git commit -m "feat(ui): Konto mit Passwortwechsel und Zwei-Faktor-Anmeldung"
```

---

### Task 16: Benutzer und Änderungsprotokoll (Admin)

**Files:**
- Create: `ui/src/lib/users/form.ts`, `ui/src/lib/users/UserForm.svelte`, `ui/src/lib/audit/view.ts`, `ui/src/routes/benutzer/+page.ts`, `ui/src/routes/benutzer/+page.svelte`, `ui/src/routes/protokoll/+page.ts`, `ui/src/routes/protokoll/+page.svelte`
- Test: `ui/src/lib/users/form.test.ts`, `ui/src/lib/users/UserForm.test.ts`, `ui/src/lib/audit/view.test.ts`

**Interfaces:**
- Consumes: `api.users`, `api.createUser`, `api.updateUser`, `api.deleteUser`, `api.audit`, `User`, `NewUser`, `AuditEntry`, `ApiError` (Task 5); `requireAdmin`, `ROLE_LABELS` (Task 7); `Dialog`, `Notice` (Task 8); `formatDateTime` (Task 1).
- Produces:
  - `users/form.ts`: `interface UserDraft { username: string; password: string; role: Role }`, `validateNewUser(draft: UserDraft): { body: NewUser } | { errors: Partial<Record<keyof UserDraft, string>> }` (Name getrimmt und klein, `^[a-z0-9._-]{3,32}$`; Passwort mindestens 10 Zeichen)
  - `UserForm.svelte`: Props `{ create: (body: NewUser) => Promise<User>; onCreated: (user: User) => void }`
  - `audit/view.ts`: `type AuditGroup = 'alle' | 'benutzer' | 'einstellungen' | 'verbraucher' | 'abrechnung'`, `AUDIT_GROUPS: { id: AuditGroup; label: string }[]`, `actionLabel(action: string): string`, `detailLines(entry: AuditEntry): string[]`, `interface AuditRow { id: number; when: string; who: string; action: string; target: string; details: string[] }`, `auditRows(entries: AuditEntry[], group: AuditGroup): AuditRow[]`
  - `benutzer/+page.ts` und `protokoll/+page.ts`: `load` ruft `requireAdmin((await parent()).user)`

- [ ] **Step 1: Failing Tests schreiben**

```ts
// ui/src/lib/users/form.test.ts
import { expect, it } from 'vitest';
import { validateNewUser } from './form';

it('prüft Benutzername und Passwort wie der Server', () => {
	expect(validateNewUser({ username: ' Max ', password: 'kurz', role: 'viewer' })).toEqual({
		errors: { password: 'Mindestens 10 Zeichen' }
	});
	expect(validateNewUser({ username: 'm', password: 'lang-genug-1', role: 'viewer' })).toEqual({
		errors: { username: '3–32 Zeichen aus a–z, 0–9, Punkt, Bindestrich, Unterstrich' }
	});
	expect(validateNewUser({ username: ' Max ', password: 'lang-genug-1', role: 'operator' })).toEqual({
		body: { username: 'max', password: 'lang-genug-1', role: 'operator' }
	});
});
```

```ts
// ui/src/lib/users/UserForm.test.ts
import { fireEvent, render, screen } from '@testing-library/svelte';
import { expect, it, vi } from 'vitest';
import { ApiError } from '$lib/api/errors';
import UserForm from './UserForm.svelte';

it('zeigt die Antwort des Servers bei einem doppelten Namen', async () => {
	const create = vi.fn().mockRejectedValueOnce(new ApiError(409, 'Benutzer „max“ existiert bereits'));
	render(UserForm, { create, onCreated: vi.fn() });
	await fireEvent.input(screen.getByLabelText('Benutzername'), { target: { value: 'max' } });
	await fireEvent.input(screen.getByLabelText('Passwort'), { target: { value: 'lang-genug-1' } });
	await fireEvent.click(screen.getByRole('button', { name: 'Anlegen' }));
	expect(await screen.findByText('Benutzer „max“ existiert bereits')).toBeInTheDocument();
});
```

```ts
// ui/src/lib/audit/view.test.ts
import { expect, it } from 'vitest';
import type { AuditEntry } from '$lib/api/schemas';
import { auditRows, detailLines } from './view';

const entry = (action: string, target: string | null, details: AuditEntry['details']): AuditEntry => ({
	id: 1,
	ts: '2026-10-09T08:00:00.123456+00:00',
	actor: 'admin',
	source: 'ui',
	action,
	target,
	details
});

it('beschreibt Einstellungsänderungen Feld für Feld', () => {
	const changes = [{ path: 'tariff.components.3.value_ct', old: null, new: 1.23 }];
	expect(detailLines(entry('settings.update', 'einstellungen', { version: 3, changes }))).toEqual([
		'Version 3',
		'tariff.components.3.value_ct: – → 1,23'
	]);
});

it('beschreibt Rollen und Neubewertungen', () => {
	expect(detailLines(entry('user.role', 'gast', { from: 'viewer', to: 'admin' }))).toEqual(['Lesen → Admin']);
	expect(
		detailLines(entry('ledger.reprice', 'abrechnung', { from: '2026-10-01', to: '2026-10-31', slots: 2976 }))
	).toEqual(['01.10.2026–31.10.2026, 2976 Viertelstunden']);
});

it('filtert nach Bereich und beschriftet Aktionen', () => {
	const rows = auditRows(
		[entry('user.create', 'gast', { role: 'viewer' }), entry('consumer.delete', 'Küche', null)],
		'verbraucher'
	);
	expect(rows.map((row) => [row.action, row.target, row.who, row.when])).toEqual([
		['Verbraucher gelöscht', 'Küche', 'admin (UI)', '09.10.2026, 10:00']
	]);
});
```

- [ ] **Step 2: Tests laufen lassen**

Run: `cd ui && pnpm vitest run src/lib/users src/lib/audit`
Expected: FAIL – Module und Komponente fehlen.

- [ ] **Step 3: Implementieren**

- `audit/view.ts`: Aktionen „Benutzer angelegt“, „Passwort geändert“, „Rolle geändert“, „Benutzer gelöscht“, „TOTP aktiviert“, „TOTP abgeschaltet“, „Einstellungen geändert“, „Verbraucher angelegt“, „Verbraucher geändert“, „Verbraucher gelöscht“, „Abrechnung neu bewertet“, Unbekanntes unverändert. Quellen „UI“, „Befehlszeile“, „Home Assistant“, „System“. Details: `settings.update` „Version N“ und je Änderung `Pfad: alt → neu`; `consumer.update` je Feld `feld: alt → neu`; `user.create` die Rolle; `user.role` „alt → neu“ mit `ROLE_LABELS`; `ledger.reprice` Zeitraum und Anzahl. Zahlen de-AT, `null` „–“, Objekte als JSON. Gruppen nach dem Präfix (`user.`, `settings.`, `consumer.`, `ledger.`).
- `UserForm.svelte`: Benutzername, Passwort, Rolle (`ROLE_LABELS`), „Anlegen“; Prüfung mit `validateNewUser`, Serverfehler als Text.
- `benutzer/+page.svelte`: Tabelle (Name, Rolle als Auswahl, TOTP, angelegt). Rollenwechsel per `api.updateUser`; ändert ein Admin die eigene Rolle, bestätigt er im `Dialog` „Du wirst danach abgemeldet.“ „Passwort setzen“ im `Dialog`, „Löschen“ mit Bestätigung. 409 (letzter Admin) und 422 als Text; danach neu laden.
- `protokoll/+page.svelte`: Bereichsfilter aus `AUDIT_GROUPS`, Liste der letzten 200 Einträge (`api.audit(200)`), Details als Zeilen.

- [ ] **Step 4: Tests laufen lassen**

Run: `cd ui && pnpm test && pnpm check`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ui/src
git commit -m "feat(ui): Benutzerverwaltung und Änderungsprotokoll für Admins"
```

---

### Task 17: End-to-End-Tests gegen das Demo-Backend

**Files:**
- Create: `ui/playwright.config.ts`, `ui/tests/e2e/helpers.ts`, `ui/tests/e2e/login.spec.ts`, `ui/tests/e2e/pages.spec.ts`, `ui/tests/e2e/settings.spec.ts`
- Modify: `.github/workflows/ci.yml` (Job `e2e`), `tests/unit/test_deploy_files.py`

**Interfaces:**
- Consumes: Demo-Backend `tests/e2e/ui_server.py` mit den Benutzern `admin`, `sicher`, `gast`, `DEMO_PASSWORD`, `DEMO_TOTP_SECRET` und eingefrorener Uhr `T_DEMO` (Task 4); alle Seiten (Tasks 7–16).
- Produces:
  - `helpers.ts`: `T_DEMO = new Date('2026-10-09T08:00:00Z')`, `DEMO_PASSWORD = 'demo-passwort-1'`, `DEMO_TOTP_SECRET = 'JBSWY3DPEHPK3PXP'`, `login(page: Page, username: string): Promise<void>` (ohne TOTP), `totp(secret: string, at: Date): string` (RFC 6238: Base32, HMAC-SHA-1 über `node:crypto`, 30 s, 6 Stellen), `watchPage(page: Page): { cspViolations: string[]; errors: string[] }` (`console`-Fehler mit „Content Security Policy“ zählen als CSP-Verstoß, andere `console`-Fehler und `pageerror` als Fehler)
  - `pnpm e2e` (setzt `pnpm build` voraus)

- [ ] **Step 1: Failing Test für die CI schreiben**

```python
# tests/unit/test_deploy_files.py
def test_ci_runs_the_ui_end_to_end_tests() -> None:
    ci = yaml.safe_load(Path(".github/workflows/ci.yml").read_text())
    job = ci["jobs"]["e2e"]
    runs = " ".join(step.get("run", "") for step in job["steps"])
    for command in (
        "uv sync --locked --extra core --extra app --extra dev",
        "pnpm install --frozen-lockfile",
        "pnpm exec playwright install --with-deps chromium",
        "pnpm build",
        "pnpm e2e",
    ):
        assert command in runs
    assert set(job["needs"]) == {"python", "ui"}
```

Run: `uv run pytest tests/unit/test_deploy_files.py -q -k end_to_end`
Expected: FAIL – Job `e2e` fehlt.

- [ ] **Step 2: Playwright einrichten und die Abläufe schreiben**

`playwright.config.ts`: `testDir: 'tests/e2e'`, `workers: 1` und `fullyParallel: false` (das Demo-Backend ist ein gemeinsamer Zustand), `retries: process.env.CI ? 1 : 0`, `use: { baseURL: 'http://127.0.0.1:8099', trace: 'retain-on-failure' }`, ein Projekt `chromium`. `webServer: { command: 'uv run python -m tests.e2e.ui_server --port 8099 --ui-dir ui/build', cwd: '..', url: 'http://127.0.0.1:8099/health', reuseExistingServer: !process.env.CI, timeout: 120_000 }`.

```ts
// ui/tests/e2e/login.spec.ts
import { expect, test } from '@playwright/test';
import { DEMO_PASSWORD, DEMO_TOTP_SECRET, T_DEMO, login, totp } from './helpers';

test('gast meldet sich an und sieht die Übersicht mit Live-Werten', async ({ page }) => {
	await page.goto('/');
	await expect(page).toHaveURL(/\/login\?next=%2F$/);
	await login(page, 'gast');
	await expect(page.getByRole('heading', { level: 1, name: 'Übersicht' })).toBeVisible();
	await expect(page.getByTestId('flow-pv')).toContainText('kW');
	await expect(page.getByText('live', { exact: true })).toBeVisible();
	await expect(page.getByRole('navigation').getByRole('link', { name: 'Benutzer' })).toHaveCount(0);
});

test('Admin mit TOTP braucht den Bestätigungscode', async ({ page }, testInfo) => {
	await page.goto('/login');
	await page.getByLabel('Benutzername').fill('sicher');
	await page.getByLabel('Passwort').fill(DEMO_PASSWORD);
	await page.getByRole('button', { name: 'Anmelden' }).click();
	// jeder Code gilt nur einmal; eine Wiederholung nimmt den nächsten Zeitschritt
	const at = new Date(T_DEMO.getTime() + 30_000 * testInfo.retry);
	await page.getByLabel('Bestätigungscode').fill(totp(DEMO_TOTP_SECRET, at));
	await page.getByRole('button', { name: 'Anmelden' }).click();
	await expect(page.getByRole('navigation').getByRole('link', { name: 'Protokoll' })).toBeVisible();
});

test('Lesende erhalten auf Admin-Seiten „Keine Berechtigung“', async ({ page }) => {
	await login(page, 'gast');
	await page.goto('/benutzer');
	await expect(page.getByText('Keine Berechtigung')).toBeVisible();
});

test('ein falsches Passwort bleibt auf der Anmeldung', async ({ page }) => {
	await page.goto('/login');
	await page.getByLabel('Benutzername').fill('gast');
	await page.getByLabel('Passwort').fill('falsch-falsch');
	await page.getByRole('button', { name: 'Anmelden' }).click();
	await expect(page.getByText('Benutzername oder Passwort falsch')).toBeVisible();
	await expect(page).toHaveURL(/\/login/);
});
```

```ts
// ui/tests/e2e/pages.spec.ts
import { expect, test } from '@playwright/test';
import { login, watchPage } from './helpers';

const PAGES = ['/', '/verlauf', '/verbraucher', '/system', '/einstellungen', '/benutzer', '/protokoll', '/konto'];

test('alle Seiten bei 360 px ohne CSP-Verstoß, Skriptfehler und waagrechtes Scrollen', async ({ page }) => {
	await page.setViewportSize({ width: 360, height: 740 });
	const watch = watchPage(page);
	await login(page, 'admin');
	for (const path of PAGES) {
		await page.goto(path);
		await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
		await page.waitForTimeout(500); // Abfragen und Diagramme rendern nach
		const overflow = await page.evaluate(
			() => document.documentElement.scrollWidth - document.documentElement.clientWidth
		);
		expect(overflow, path).toBeLessThanOrEqual(0);
	}
	expect(watch.cspViolations).toEqual([]);
	expect(watch.errors).toEqual([]);
});

test('Verlauf zeigt Diagramm und Tagesbilanz', async ({ page }) => {
	await login(page, 'gast');
	await page.goto('/verlauf');
	await page.getByLabel('Erster Tag').fill('2026-10-09');
	await page.getByLabel('Letzter Tag').fill('2026-10-09');
	await expect(page.locator('canvas').first()).toBeVisible();
	await expect(page.getByRole('cell', { name: 'Fr., 09.10.' })).toBeVisible();
});

test('Verbraucher zeigen „Sonstiges“, System zeigt Signale', async ({ page }) => {
	await login(page, 'gast');
	await page.goto('/verbraucher');
	await expect(page.getByText('Obergeschoss')).toBeVisible();
	await expect(page.getByText('Sonstiges').first()).toBeVisible();
	await page.goto('/system');
	await page.getByLabel('Signal suchen').fill('pv.huawei.power');
	await expect(page.getByRole('cell', { name: 'pv.huawei.power_w' })).toBeVisible();
});
```

```ts
// ui/tests/e2e/settings.spec.ts
import { expect, test } from '@playwright/test';
import { login } from './helpers';

const LOSS = 'Netzverlustentgelt (ct/kWh netto)';
const TAX = 'Elektrizitätsabgabe (ct/kWh netto)';

test('Admin ändert das Netzverlustentgelt und bewertet neu', async ({ page }) => {
	await login(page, 'admin');
	await page.goto('/einstellungen');
	await page.getByRole('button', { name: 'Bearbeiten' }).click();
	await page.getByLabel(LOSS).fill('1,23');
	await page.getByRole('button', { name: 'Speichern' }).click();
	await expect(page.getByText(/Version \d+ gespeichert/)).toBeVisible();
	await page.getByRole('button', { name: 'Neu bewerten' }).click();
	await expect(page.getByText(/\d+ Viertelstunden neu bewertet\./)).toBeVisible();
});

test('Konflikt bei gleichzeitiger Änderung behält die Eingaben', async ({ page }) => {
	await login(page, 'admin');
	await page.goto('/einstellungen');
	await page.getByRole('button', { name: 'Bearbeiten' }).click();
	await page.getByLabel(TAX).fill('0,1');
	// ein zweiter Admin speichert dazwischen
	const current = await (await page.request.get('/api/settings')).json();
	const csrf = (await page.context().cookies()).find((cookie) => cookie.name === 'bindaems_csrf');
	const monthly = { ...current.settings.feed_in.monthly_ct, '2026-08': 6.5 };
	const response = await page.request.put('/api/settings', {
		data: { base_version: current.version, settings: { ...current.settings, feed_in: { monthly_ct: monthly } } },
		headers: { 'X-CSRF-Token': csrf?.value ?? '' }
	});
	expect(response.ok()).toBe(true);
	await page.getByRole('button', { name: 'Speichern' }).click();
	await expect(page.getByText(/inzwischen geändert/)).toBeVisible();
	await expect(page.getByLabel(TAX)).toHaveValue('0,1');
});
```

- [ ] **Step 3: Abläufe lokal laufen lassen**

Run: `cd ui && pnpm build && pnpm e2e`
Expected: alle Abläufe PASS. Schlägt einer fehl, ist das ein Befund im UI oder im Demo-Backend; die Tests werden nicht aufgeweicht.

- [ ] **Step 4: CI-Job `e2e` ergänzen**

`.github/workflows/ci.yml`, Job `e2e` (`needs: [python, ui]`): `actions/checkout@v4`; `astral-sh/setup-uv@v6` mit Python 3.12; `uv sync --locked --extra core --extra app --extra dev`; `pnpm/action-setup@v4` (`package_json_file: ui/package.json`); `actions/setup-node@v4` (Node 22, pnpm-Cache); in `ui`: `pnpm install --frozen-lockfile`, `pnpm exec playwright install --with-deps chromium`, `pnpm build`, `pnpm e2e`; bei Fehler `actions/upload-artifact@v4` mit `ui/playwright-report` und `ui/test-results`.

Run: Prüfskript
Expected: alles grün, auch `test_ci_runs_the_ui_end_to_end_tests`.

- [ ] **Step 5: Commit**

```bash
git add ui/playwright.config.ts ui/tests .github/workflows/ci.yml tests/unit/test_deploy_files.py
git commit -m "test(ui): Playwright-Abläufe gegen das Demo-Backend, E2E-Job in der CI"
```

---

### Task 18: Bedienungsdoku und Abnahme-Checkliste

**Files:**
- Modify: `docs/betrieb.md` (neuer Abschnitt „12. Web-UI“), `docs/verification/README.md` (Verweis auf die Checkliste)
- Create: `docs/verification/abnahme-phase-1.md`
- Test: `tests/unit/test_docs.py`

**Interfaces:**
- Consumes: alle Seiten des UI; Spec 18 (Abnahme Phase 1); Prüfprotokolle aus 1a und 1b (`docs/verification/README.md`).
- Produces: Doku für Betreiber und eine Checkliste, die an der Anlage ausgefüllt wird.

- [ ] **Step 1: Failing Tests schreiben**

```python
# tests/unit/test_docs.py
from pathlib import Path


def test_acceptance_checklist_covers_spec_18() -> None:
    text = Path("docs/verification/abnahme-phase-1.md").read_text(encoding="utf-8")
    for criterion in (
        "7 Tage lückenlose Aufzeichnung",
        "±2 %",
        "VRM",
        "Preise und Prognose sichtbar",
        "keine Schreibpfade",
    ):
        assert criterion in text


def test_operations_guide_describes_the_web_ui() -> None:
    text = Path("docs/betrieb.md").read_text(encoding="utf-8")
    for topic in ("## 12. Web-UI", "App installieren", "Sitzung", "Bestätigungscode"):
        assert topic in text
```

- [ ] **Step 2: Tests laufen lassen**

Run: `uv run pytest tests/unit/test_docs.py -q`
Expected: FAIL – Checkliste und Abschnitt fehlen.

- [ ] **Step 3: Doku schreiben**

- `docs/betrieb.md`, „12. Web-UI“:
  - Adresse über den Reverse Proxy, Anmeldung und Rollen.
  - Zwei-Faktor-Anmeldung unter „Konto“ (QR-Code, Bestätigungscode).
  - Leerlauf: Admins nach 30 min ohne Bedienung abgemeldet, auch bei offener Übersicht; „angemeldet bleiben“ 30 Tage nur für Bedienen und Lesen.
  - App installieren: Android/Chrome über Menü → „App installieren“, iOS/Safari über Teilen → „Zum Home-Bildschirm“.
  - Hell und dunkel.
  - Seitenüberblick.
  - Live-Anzeige („live“, „veraltet“, „core getrennt“) und was bei „getrennt“ zu prüfen ist.
  - Demo-Backend nur für die Entwicklung.
- `docs/verification/abnahme-phase-1.md`, je Kriterium aus Spec 18 ein Abschnitt mit Prüfschritten, Fundort im UI, Nachweis und Feldern für Datum, Ergebnis und Unterschrift:
  1. **7 Tage lückenlose Aufzeichnung:** „Verlauf“ mit 7 Tagen, Reihen Netz, PV, Akku und Haus ohne Lücken; „Tagesbilanz“ mit voller Abdeckung an allen 7 Tagen (96 von 96, an Umstellungstagen 92 oder 100); „System“ meldet „InfluxDB: alles übertragen“.
  2. **Tages-Energiebilanz innerhalb ±2 % von VRM:** für mindestens 3 Tage PV, Netzbezug, Einspeisung und Verbrauch aus VRM gegen Tagesbilanz und Zählerstände; Tabelle mit Abweichung in %.
  3. **Preise und Prognose sichtbar:** „Übersicht“ mit Preis jetzt und Diagramm für heute und morgen; „System“ mit erkanntem Brutto/Netto und PV-Prognose; Verweis auf das Prüfprotokoll „Preise“.
  4. **Nachweislich keine Schreibpfade:**
     - Im Code publiziert der core am Cerbo nur `R/<portal>/keepalive`, liest über Modbus nur und schickt an Wallbox, Tessie und Home Assistant nur `GET`. Die app schreibt nur nach InfluxDB (`price`, `forecast`, `ledger`) und auf ihre eigenen HA-Topics.
     - An der Anlage 24 h `mosquitto_sub -v -t 'W/#'` am Cerbo ohne Nachricht von BindaEMS.
     - EVCS-Modus und ESS-Einstellungen vorher und nachher unverändert (Prüfprotokoll Teil 1).
- `docs/verification/README.md`: Verweis auf die Checkliste.

- [ ] **Step 4: Tests laufen lassen**

Run: Prüfskript
Expected: alles grün.

- [ ] **Step 5: Commit**

```bash
git add docs tests/unit/test_docs.py
git commit -m "docs: Web-UI im Betriebshandbuch, Checkliste für die Abnahme der Phase 1"
```

---

## Anhang A: Antwortformen der API (Stand nach Plan 1b und den Nacharbeiten)

Die Vertragsdateien aus Task 4 zeigen Beispiele; dieser Anhang legt Formen und Nullbarkeit fest. `ISO` ist ein Zeitstempel mit Versatz (`"2026-10-09T08:00:00+00:00"`, oft mit Mikrosekunden), Datumsangaben sind `"YYYY-MM-DD"`, Uhrzeiten `"HH:MM:SS"`.

**Allgemein**
- Fehler: `{ detail: string }` oder bei Validierung `{ detail: { type: string; loc: (string | number)[]; msg: string; input?: unknown; ctx?: object }[] }`. 401 „Nicht angemeldet“, 403 „Keine Berechtigung“ bzw. „CSRF-Token fehlt oder ist ungültig“, 500 „Interner Fehler“, 429 mit `Retry-After`.
- Körper immer mit `Content-Type: application/json`; unbekannte Felder ergeben 422.
- Unbekannte Pfade mit anderen Methoden als GET ergeben 405 (die SPA-Route fängt GET), Pfade mit abschließendem `/` ergeben 404.
- Rollen: öffentlich nur `POST /api/auth/login` und `GET /health`. Admin nur bei `/api/users*`, `/api/audit`, `PUT /api/settings`, `/api/settings/versions`, `/api/settings/export`, `POST /api/settings/import`, `/api/consumers/candidates`, `POST`/`PATCH`/`DELETE /api/consumers*`, `POST /api/prices/refresh` und `POST /api/ledger/reprice`. Alles andere: angemeldet (Lesen).

**Gemeinsame Typen**
```ts
type Role = 'admin' | 'operator' | 'viewer';
type User = { id: number; username: string; role: Role; totp_enabled: boolean; created_at: ISO };
type ComponentStatus = { name: string; ok: boolean; message: string; since: ISO | null; details: object };
type Alarm = { id: string; severity: 'info' | 'warning' | 'error'; message: string; since: ISO };
type Reading = { v: number | string | boolean | null; ts: ISO; q: 'ok' | 'stale' | 'invalid' };
type Phase = 'L1' | 'L2' | 'L3';
type Derived = {
	grid_w: number | null; // + = Bezug
	pv_total_w: number | null;
	consumption_w: number | null;
	battery_ac_w: number | null;
	battery_w: number | null; // + = Laden
	house_load_w: number | null;
	consumption_l: Record<Phase, number | null>;
	wallbox_w: Record<string, number | null>;
	wallbox_phase_a: Record<string, Record<Phase, number>>;
	phase_import_a: Record<Phase, number | null>;
	balance_residual_w: number | null;
};
type CoreState = { ts: ISO; signals: Record<string, Reading>; derived: Derived };
type CoreHealth = {
	mode: 'OBSERVE';
	version: string;
	adapters: { name: string; connected: boolean; last_ok: ISO | null; last_error: string | null; error_count: number }[];
	selfcheck: { id: string; status: 'ok' | 'warn' | 'fail' | 'unknown'; message: string }[];
	alarms: Alarm[];
	cycle_ms_p95: number | null;
};
```

**Anmeldung und Konto** (`/api/auth`)
- `POST /login` `{ username ≤64; password ≤1024; totp?: string | null ≤16; remember?: boolean }` → `{ user: User }` und Cookies; 401 „Benutzername oder Passwort falsch“; 401 `{ detail: "Bestätigungscode erforderlich", totp_required: true }`; 429.
- `POST /logout` → 204. `GET /me` → `{ user: User }`.
- `POST /password` `{ old_password; new_password }` → 204; 400 „Altes Passwort falsch“; 429; 422 Passwortregeln.
- `POST /totp/setup` `{ password }` → `{ secret: string; uri: string }`; 400 „Passwort falsch“; 409 „bereits aktiv“; 429.
- `POST /totp/enable` `{ code }` → 204; 400 „Bestätigungscode ungültig“. `POST /totp/disable` `{ password }` → 204; 400; 429.

**Benutzer und Protokoll** (Admin)
- `GET /api/users` → `User[]`; `POST /api/users` `{ username; password; role }` → 201 `User`; 409 „Benutzer „…“ existiert bereits“; 422 „Benutzername: 3–32 Zeichen …“.
- `PATCH /api/users/{id}` `{ role?: Role | null; password?: string | null }` → `User` (eine Transaktion); 404; 409 letzter Admin. `DELETE /api/users/{id}` → 204; 404; 409.
- `GET /api/audit?limit=1..1000` → `{ id: number; ts: ISO; actor: string; source: 'ui' | 'ha' | 'cli' | 'system'; action: string; target: string | null; details: object | null }[]` (neueste zuerst).

**Live und System**
- `GET /api/state` → `{ core_connected: boolean; updated_at: ISO | null; state: CoreState | null; alarms: Alarm[] }`.
- `GET /api/system` → `{ core: { connected: boolean; health: CoreHealth | null }; components: ComponentStatus[]; warnings: string[] }`.
- `GET /health` → `{ status: 'ok'; version: string }`.
- `WS /api/live`: Nachrichten `{ type: 'hello'; data: { core_connected: boolean; state: CoreState | null; alarms: Alarm[] } }`, `{ type: 'state'; data: CoreState }` (etwa jede Sekunde), `{ type: 'alarm'; data: Alarm[] }` (bei Änderung), `{ type: 'core'; data: { connected: boolean } }`. Schließcodes 4401 (Sitzung fehlt oder abgelaufen; Prüfung alle 60 s) und 4403 (fremde Herkunft).

**Einstellungen**
```ts
type TimeWindow = { months: number[]; weekdays: number[]; start: string; end: string; factor: number | null; value_ct: number | null };
type PriceComponent = {
	id: string; name: string; kind: 'energy_per_kwh'; source: 'fixed' | 'spot';
	value_ct: number | null; vat: boolean; valid_from: string | null; valid_until: string | null; windows: TimeWindow[];
};
type RuntimeSettings = {
	prices: { vat_mode: 'auto' | 'net' | 'gross'; vat_fallback: 'net' | 'gross'; reference_source: 'energy_charts' | 'awattar' };
	tariff: { vat_pct: number; components: PriceComponent[]; fixed_price_gross_ct: number };
	feed_in: { monthly_ct: Record<string, number> };
	pv_model: { performance_ratio: number; temp_coeff_pct_per_k: number; noct_c: number };
};
type SettingsMeta = { version: number; created_at: ISO; actor: string; source: 'ui' | 'ha' | 'cli' | 'system'; comment: string | null };
type SettingsCurrent = SettingsMeta & { settings: RuntimeSettings; warnings: string[] };
```
- `GET /api/settings` → `SettingsCurrent`. `PUT /api/settings` `{ base_version: number; settings: RuntimeSettings; comment?: string | null }` → `SettingsCurrent`; ersetzt alles; 409 „Die Einstellungen wurden inzwischen geändert (aktuell Version N).“; gleiche Einstellungen → keine neue Version.
- `GET /api/settings/versions` → `SettingsMeta[]` (höchstens 50). `GET /api/settings/export` → YAML-Datei. `POST /api/settings/import` `{ yaml }` → `SettingsCurrent`; 422 mit Text (Zeilen `pfad: meldung`).
- `GET /api/limits` → `{ grid: { phases: 3; voltage_nominal_v; fuse_a; fuse_margin_a }; battery: { usable_kwh; reserve_soc_pct; max_charge_w; max_discharge_w; soc_max_pct }; victron: { max_grid_charge_setpoint_w; persistent_writes: { per_hour; per_day }; watchdog: { heartbeat_s; timeout_s } }; wallboxes: Record<string, { type: 'victron_evcs_ns'; min_a; max_a; safe_a; phase_map: Phase[]; live_allowed: boolean } | { type: 'tesla_wall_connector_gen3'; max_a; phase_map: Phase[] }>; vehicles: Record<string, { name: string; usable_kwh; phases: 1 | 2 | 3; min_a; max_a; default_wallbox: string; live_allowed: boolean }> }` (alle nicht genannten Werte `number`).

**Verbraucher**
```ts
type Consumer = { id: number; name: string; group: string | null; parent_id: number | null; color: string; source_kind: 'core' | 'ha'; power_ref: string; power_unit: 'W' | 'kW' | null; sort: number };
type TreeNode = { id: number | null; name: string; color: string | null; power_w: number | null; other_w: number | null; mismatch: boolean; children: TreeNode[] };
type ConsumerInput = { name: string; group?: string | null; parent_id?: number | null; color: string; source_kind: 'core' | 'ha'; power_ref: string; power_unit?: 'W' | 'kW' | null; sort?: number };
```
- `GET /api/consumers` → `{ tree: TreeNode; consumers: Consumer[] }`; Wurzel „Haus“ mit `power_w` = Hauslast; „Sonstiges“ ist kein Knoten, sondern `other_w` jedes Knotens mit Kindern (negativ → 0 und `mismatch: true`; `null`, wenn ein Wert fehlt).
- `GET /api/consumers/candidates` (Admin, kann wegen InfluxDB dauern) → `{ core: { ref: string }[]; ha: { entity_id: string; unit: 'W' | 'kW' }[] }`.
- `POST /api/consumers` → 201 `Consumer`; `PATCH /api/consumers/{id}` mit vollständigem `ConsumerInput` → `Consumer`; `DELETE` → 204; 404 „Verbraucher nicht gefunden“; 409 „Verbraucher hat Unterverbraucher“; 422 (Regeln in Task 12).

**Verlauf**
- `GET /api/history/catalog` → `{ id: string; label: string; unit: string }[]` (`grid`, `pv`, `battery`, `house`, `consumption`, `wallbox.<n>`, `soc.battery`, `soc.<fahrzeug>`, `price`, `forecast.pv`).
- `GET /api/history?series=a,b&from=…Z&to=…Z` (1–8 Reihen, höchstens 400 Tage) → `{ rp: 'raw' | 'long'; step_s: number; series: Record<string, { label: string; unit: string; points: [number, number][] }> }` (Epoch-ms, Mittelwert; Lücken fehlen); 502 „InfluxDB nicht erreichbar“.

**Preise, Prognose, Abrechnung**
```ts
type PriceSlot = { start: ISO; spot_net_ct: number; import_net_ct: number; import_gross_ct: number; feed_in_ct: number | null; origin: 'primary' | 'fallback'; missing: string[] };
type PriceStatus = {
	last_attempt: ISO | null; last_success: ISO | null; vat_mode: 'net' | 'gross' | null;
	vat_detection: { result: 'net' | 'gross' | null; ratio: number | null; slots: number } | null;
	days: { date: string; origin: 'primary' | 'fallback' | null; findings: string[] }[];
	errors: string[];
};
type ForecastSlot = { start: ISO; p50_w: number };
type DaySummary = {
	date: string; slots: number; expected_slots: number; coverage: number;
	pv_kwh: number; import_kwh: number; export_kwh: number; house_kwh: number; wallbox_kwh: Record<string, number>;
	battery_charge_kwh: number; battery_discharge_kwh: number; consumption_kwh: number; counter_kwh: Record<string, number>;
	cost_eur: number | null; revenue_eur: number | null; net_cost_eur: number | null; autarky: number | null;
	self_consumption: number | null; savings_no_plant_eur: number | null; savings_same_import_eur: number | null;
};
```
- `GET /api/prices[?from&to]` (Standard heute 00:00 Wien bis +2 Tage, höchstens 31 Tage) → `{ status: PriceStatus; slots: PriceSlot[] }`.
- `GET /api/prices/now` → `{ now: PriceSlot | null; next_3h: PriceSlot[] }`. `POST /api/prices/refresh` (Admin, bis etwa 30 s) → `PriceStatus`.
- `GET /api/forecast/pv` → `{ issued_at: ISO | null; source: 'open_meteo' | null; slots: ForecastSlot[]; days: { date: string; kwh: number | null }[]; status: ComponentStatus }`.
- `GET /api/ledger/days?from=YYYY-MM-DD&to=YYYY-MM-DD` (inklusiv, höchstens 400 Tage) → `DaySummary[]` (auch Tage ohne Daten).
- `POST /api/ledger/reprice` `{ from; to }` (Admin) → `{ repriced: number }`.
