import { render, screen, within } from '@testing-library/svelte';
import * as v from 'valibot';
import { expect, it, vi } from 'vitest';
import pricesJson from '$lib/api/contract/prices.json';
import { PricesResponseSchema, type CoreHealth, type SystemResponse } from '$lib/api/schemas';
import { system } from '$lib/testing/fixtures';
import Page from './+page.svelte';

const { api } = vi.hoisted(() => ({
	api: {
		system: vi.fn(),
		prices: vi.fn(),
		forecast: vi.fn(),
		health: vi.fn(),
		refreshPrices: vi.fn()
	}
}));
vi.mock('$lib/api/endpoints', async (original) => ({
	...(await original<typeof import('$lib/api/endpoints')>()),
	api
}));
vi.mock('$app/state', () => ({
	page: { data: { user: { id: 1, username: 'admin', role: 'admin' } } }
}));

it('zeigt gleichlautende Hinweise und Preisfehler ohne abzustürzen', async () => {
	const warning = 'Tarifbestandteil „Netznutzungsentgelt“ hat noch keinen Wert.';
	const error = 'Zeitüberschreitung beim Abruf';
	const prices = v.parse(PricesResponseSchema, pricesJson);
	api.system.mockResolvedValue({ ...system(), warnings: [warning, warning] });
	api.prices.mockResolvedValue({ ...prices, status: { ...prices.status, errors: [error, error] } });
	api.forecast.mockReturnValue(new Promise(() => {}));
	api.health.mockReturnValue(new Promise(() => {}));
	render(Page);
	expect(await screen.findAllByText(warning)).toHaveLength(2);
	expect(await screen.findAllByText(error)).toHaveLength(2);
});

// --- Betriebszustand, Aktoren und Watchdog -------------------------------------------------

/** Rendert die Seite mit dieser Antwort von /api/system; die übrigen Abfragen bleiben offen. */
function showSystem(response: SystemResponse) {
	api.system.mockResolvedValue(response);
	api.prices.mockReturnValue(new Promise(() => {}));
	api.forecast.mockReturnValue(new Promise(() => {}));
	api.health.mockReturnValue(new Promise(() => {}));
	render(Page);
}

/** Gesundheitsbericht des core aus der Vertragsdatei (Demo-Welt: der Watchdog „wartet“) */
function demoHealth(): CoreHealth {
	const { health } = system().core;
	if (health === null) throw new Error('system.json enthält keinen Gesundheitsbericht');
	return health;
}

/** Rendert die Seite mit dem Bericht des core aus der Vertragsdatei, einzelne Felder überschrieben */
function show(changes: Partial<CoreHealth>) {
	const base = system();
	showSystem({ ...base, core: { ...base.core, health: { ...demoHealth(), ...changes } } });
}

function watchdogWith(changes: Partial<CoreHealth['watchdog']>): CoreHealth['watchdog'] {
	return { ...demoHealth().watchdog, ...changes };
}

/** Die Karte mit dieser Überschrift */
function card(title: string): HTMLElement {
	const section = screen.getByRole('heading', { level: 2, name: title }).closest('section');
	if (section === null) throw new Error(`Karte „${title}“ fehlt`);
	return section;
}

function following(element: HTMLElement): HTMLElement {
	const next = element.nextElementSibling;
	if (!(next instanceof HTMLElement)) throw new Error('Es folgt kein Element');
	return next;
}

/** Der Wert (`dd`) zur Beschriftung (`dt`) in einer Kennzahlenliste */
function fact(scope: HTMLElement, label: string): HTMLElement {
	return following(within(scope).getByText(label, { selector: 'dt' }));
}

function listItems(scope: HTMLElement): (string | null)[] {
	return within(scope)
		.getAllByRole('listitem')
		.map((item) => item.textContent);
}

it('zeigt die eingeschränkte Betriebsart, den gesperrten Aktor und den ausgelösten Watchdog', async () => {
	const reason = 'Victron (ESS) gesperrt: Dynamic ESS ist aktiv (Modus 1).';
	show({
		mode: 'DEGRADED',
		mode_reasons: [reason],
		actuators: [
			{
				id: 'victron',
				label: 'Victron (ESS)',
				requested: 'live',
				effective: 'dry_run',
				live_allowed: true,
				reasons: [],
				locked: ['Dynamic ESS ist aktiv (Modus 1).'],
				watchdog_needed: true
			}
		],
		watchdog: watchdogWith({ state: 'tripped', version: '0.9.0' })
	});
	expect(await screen.findByText('Eingeschränkt')).toBeInTheDocument();
	expect(screen.getByText(reason)).toBeInTheDocument();
	const watchdog = within(card('Watchdog am Cerbo'));
	expect(watchdog.getByText('ausgelöst')).toBeInTheDocument();
	expect(watchdog.getByText('0.9.0 (erwartet 1.1.0)')).toBeInTheDocument();
	const actuators = within(card('Aktoren'));
	expect(actuators.getByText('gesperrt: Dynamic ESS ist aktiv (Modus 1).')).toBeInTheDocument();
});

it('nennt Beginn und Gründe der Betriebsart und die Probleme der Eingangsdaten', async () => {
	show({
		mode: 'SAFE',
		mode_since: '2026-10-09T07:55:00+00:00',
		mode_reasons: ['Cerbo-Daten veraltet: grid.l1.power_w.', 'Netzausfall (Inselbetrieb).'],
		inputs: { ok: false, problems: ['Netzspannung L1 unplausibel: 300.0 V', 'Sprung im Akku-SOC.'] }
	});
	await screen.findByText('Sicherer Zustand');
	const mode = fact(card('core'), 'Betriebsart');
	expect(mode).toHaveTextContent(/^Sicherer Zustand seit 09\.10\.2026, 09:55$/);
	expect(listItems(following(mode))).toEqual([
		'Cerbo-Daten veraltet: grid.l1.power_w.',
		'Netzausfall (Inselbetrieb).'
	]);
	const inputs = fact(card('core'), 'Eingangsdaten');
	expect(listItems(inputs)).toEqual([
		'Netzspannung L1 unplausibel: 300.0 V',
		'Sprung im Akku-SOC.'
	]);
	expect(inputs).not.toHaveTextContent('in Ordnung');
});

it('zeigt ohne Gründe und Probleme nur die Betriebsart und „in Ordnung“', async () => {
	show({ mode: 'CONTROL', mode_since: null, mode_reasons: [], inputs: { ok: true, problems: [] } });
	await screen.findByText('Regeln');
	const mode = fact(card('core'), 'Betriebsart');
	expect(mode).toHaveTextContent(/^Regeln$/);
	expect(following(mode)).toHaveTextContent(/^Eingangsdaten$/); // keine Liste der Gründe dazwischen
	expect(fact(card('core'), 'Eingangsdaten')).toHaveTextContent(/^in Ordnung$/);
});

it('listet die Aktoren mit Betriebsart, Freigabe und Hinweisen', async () => {
	show({
		actuators: [
			{
				id: 'victron',
				label: 'Victron (ESS)',
				requested: 'live',
				effective: 'dry_run',
				live_allowed: false,
				reasons: ['In config.yaml nicht für den Live-Betrieb freigegeben (live_allowed).'],
				locked: ['Dynamic ESS ist aktiv (Modus 1).', 'EVCS-Modus nicht verfügbar.'],
				watchdog_needed: true
			},
			{
				id: 'vehicle.tesla',
				label: 'Tesla Model 3',
				requested: 'dry_run',
				effective: 'dry_run',
				live_allowed: true,
				reasons: [],
				locked: [],
				watchdog_needed: true
			}
		]
	});
	const actuators = within(card('Aktoren'));
	const headers = await actuators.findAllByRole('columnheader');
	expect(actuators.getByRole('table').parentElement).toHaveClass('scroll'); // schmale Schirme scrollen
	expect(headers.map((header) => header.textContent)).toEqual([
		'Aktor',
		'Angefordert',
		'Wirksam',
		'live erlaubt',
		'Hinweise'
	]);
	const [victron, tesla] = actuators.getAllByRole('row').slice(1);
	const cells = (row: HTMLElement) => within(row).getAllByRole('cell');
	expect(
		cells(victron)
			.slice(0, 4)
			.map((cell) => cell.textContent?.trim())
	).toEqual(['Victron (ESS)', 'live', 'Dry-Run', 'nein']);
	expect(listItems(cells(victron)[4])).toEqual([
		'In config.yaml nicht für den Live-Betrieb freigegeben (live_allowed).',
		'gesperrt: Dynamic ESS ist aktiv (Modus 1).',
		'gesperrt: EVCS-Modus nicht verfügbar.'
	]);
	expect(cells(tesla).map((cell) => cell.textContent?.trim())).toEqual([
		'Tesla Model 3',
		'Dry-Run',
		'Dry-Run',
		'ja',
		'–'
	]);
});

it('zeigt gleichlautende Gründe, Probleme und Hinweise ohne abzustürzen', async () => {
	const same = 'Netzausfall (Inselbetrieb).';
	show({
		mode_reasons: [same, same],
		inputs: { ok: false, problems: [same, same] },
		actuators: [
			{
				id: 'victron',
				label: 'Victron (ESS)',
				requested: 'live',
				effective: 'dry_run',
				live_allowed: true,
				reasons: [same, same],
				locked: [],
				watchdog_needed: true
			}
		]
	});
	expect(await screen.findAllByText(same)).toHaveLength(6);
});

it.each([
	['armed', false, 'scharf', 'ok'],
	['waiting', false, 'wartet', 'warn'],
	['disabled', true, 'deaktiviert', 'warn'],
	['tripped', false, 'ausgelöst', 'error'],
	['unknown', true, 'keine Antwort', 'error'],
	['unknown', false, 'keine Antwort', 'unknown']
] as const)(
	'Watchdog „%s“ (gebraucht: %s) steht als „%s“ mit dem Punkt „%s“',
	async (state, required, label, dot) => {
		show({ watchdog: watchdogWith({ state, required }) });
		const watchdog = card('Watchdog am Cerbo');
		expect(await within(watchdog).findByRole('img', { name: label })).toHaveClass(dot);
		expect(fact(watchdog, 'Zustand')).toHaveTextContent(label);
	}
);

it('zeigt Heartbeat, Auslösungen, letzte Auslösung, fehlgeschlagene Aktionen und Version', async () => {
	show({
		watchdog: watchdogWith({
			state: 'tripped',
			heartbeat: 'released',
			trip_count: 3,
			last_trip: '2026-10-09T07:30:00+00:00',
			last_trip_failures: 1,
			version: '1.1.0'
		})
	});
	const watchdog = card('Watchdog am Cerbo');
	await within(watchdog).findByText('freigegeben');
	expect(fact(watchdog, 'Heartbeat')).toHaveTextContent(/^freigegeben$/);
	expect(fact(watchdog, 'Auslösungen')).toHaveTextContent(/^3$/);
	expect(fact(watchdog, 'Letzte Auslösung')).toHaveTextContent(/^09\.10\.2026, 09:30$/);
	expect(fact(watchdog, 'Fehlgeschlagene Aktionen')).toHaveTextContent(/^1$/);
	expect(fact(watchdog, 'Version')).toHaveTextContent(/^1\.1\.0$/);
});

it('zeigt den wartenden Watchdog der Demo ohne bisherige Auslösung', async () => {
	showSystem(system());
	const watchdog = card('Watchdog am Cerbo');
	expect(await within(watchdog).findByText('wartet')).toBeInTheDocument();
	expect(fact(watchdog, 'Heartbeat')).toHaveTextContent(/^aus \(kein Aktor live\)$/);
	expect(fact(watchdog, 'Auslösungen')).toHaveTextContent(/^0$/);
	expect(fact(watchdog, 'Letzte Auslösung')).toHaveTextContent(/^nie$/);
	expect(fact(watchdog, 'Fehlgeschlagene Aktionen')).toHaveTextContent(/^0$/);
	expect(fact(watchdog, 'Version')).toHaveTextContent(/^1\.1\.0$/);
});

it('zeigt ohne Antwort des Watchdogs Striche statt „nie“ und 0', async () => {
	show({
		watchdog: watchdogWith({
			state: 'unknown',
			trip_count: null,
			last_trip: null,
			last_trip_failures: null,
			version: null
		})
	});
	const watchdog = card('Watchdog am Cerbo');
	await within(watchdog).findByText('keine Antwort');
	for (const label of ['Auslösungen', 'Letzte Auslösung', 'Fehlgeschlagene Aktionen', 'Version']) {
		expect(fact(watchdog, label), label).toHaveTextContent(/^–$/);
	}
});

it('zeigt ohne Bericht des core Aktoren und Watchdog als nicht verfügbar', async () => {
	showSystem({ ...system(), core: { connected: false, health: null } });
	expect(await within(card('Aktoren')).findByText('Keine Daten vom core')).toBeInTheDocument();
	expect(within(card('Watchdog am Cerbo')).getByText('Keine Daten vom core')).toBeInTheDocument();
	expect(fact(card('core'), 'Betriebsart')).toHaveTextContent(/^–$/);
	expect(fact(card('core'), 'Eingangsdaten')).toHaveTextContent(/^–$/);
});
