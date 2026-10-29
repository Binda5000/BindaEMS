import { fireEvent, render, screen, waitFor, within } from '@testing-library/svelte';
import * as v from 'valibot';
import { beforeEach, expect, it, vi } from 'vitest';
import controlJson from '$lib/api/contract/control.json';
import pricesJson from '$lib/api/contract/prices.json';
import { ApiError } from '$lib/api/errors';
import {
	ControlResponseSchema,
	PricesResponseSchema,
	type ControlReply,
	type CoreHealth,
	type Intent,
	type Role,
	type SystemResponse
} from '$lib/api/schemas';
import { limits, system } from '$lib/testing/fixtures';
import Page from './+page.svelte';

const { api, user } = vi.hoisted(() => ({
	api: {
		system: vi.fn(),
		prices: vi.fn(),
		forecast: vi.fn(),
		loadForecast: vi.fn(),
		forecastQuality: vi.fn(),
		limits: vi.fn(),
		health: vi.fn(),
		refreshPrices: vi.fn(),
		control: vi.fn(),
		setRegulation: vi.fn(),
		setActuators: vi.fn(),
		acknowledge: vi.fn()
	},
	user: { role: 'admin' as Role }
}));
vi.mock('$lib/api/endpoints', async (original) => ({
	...(await original<typeof import('$lib/api/endpoints')>()),
	api
}));
vi.mock('$app/state', () => ({
	page: {
		data: {
			get user() {
				return { id: 1, username: 'bedienen', role: user.role };
			}
		}
	}
}));

function baseIntent(changes: Partial<Intent> = {}): Intent {
	const { intent } = v.parse(ControlResponseSchema, controlJson);
	if (intent === null) throw new Error('control.json enthält keine Absicht');
	return { ...intent, ...changes };
}

function reply(changes: Partial<ControlReply> = {}): ControlReply {
	const intent = baseIntent();
	return { result: 'accepted', version: intent.version + 1, details: [], intent, ...changes };
}

beforeEach(() => {
	user.role = 'admin';
	for (const mock of Object.values(api)) mock.mockReset();
	// die Prognose-Karten warten, solange ein Test sie nicht füllt
	api.loadForecast.mockReturnValue(new Promise(() => {}));
	api.forecastQuality.mockReturnValue(new Promise(() => {}));
	api.limits.mockReturnValue(new Promise(() => {}));
	api.control.mockResolvedValue({ core_connected: true, intent: baseIntent() });
});

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

it('zeigt die Karten Lastprognose und Prognosegüte', async () => {
	showSystem(system());
	expect(
		await screen.findByRole('heading', { level: 2, name: 'Lastprognose' })
	).toBeInTheDocument();
	expect(screen.getByRole('heading', { level: 2, name: 'Prognosegüte' })).toBeInTheDocument();
	expect(api.loadForecast).toHaveBeenCalledTimes(1);
	expect(api.forecastQuality).toHaveBeenCalledWith(14, expect.anything());
});

// --- Komponenten und Selbstprüfung ---------------------------------------------------------

it('zählt die Komponenten in der Kopfzeile und stellt die gestörte nach vorn', async () => {
	showSystem(system());
	const components = card('Komponenten');
	expect(await within(components).findByText('10 von 11 in Ordnung')).toBeInTheDocument();
	const rows = within(components).getAllByRole('listitem');
	expect(rows).toHaveLength(11);
	expect(within(rows[0]!).getByText('Home Assistant')).toBeInTheDocument();
	expect(within(rows[0]!).getByRole('img', { name: 'gestört' })).toBeInTheDocument();
	expect(rows[1]).toHaveTextContent(/^\s*core\s*verbunden\s*$/);
});

it('benennt Komponenten deutsch und wiederholt den Namen nicht in der Meldung', async () => {
	showSystem(system());
	const components = card('Komponenten');
	const row = (name: string) => {
		const label = within(components).getByText(name, { selector: '.label' });
		return label.closest('li') as HTMLElement;
	};
	await within(components).findByText('Planer', { selector: '.label' });
	for (const name of ['Lastmodell', 'Fahrverhalten', 'Wärmemodell', 'Planer']) {
		expect(row(name)).toBeInTheDocument();
	}
	expect(within(row('Preise')).getByText('bis 09.10. 23:45')).toBeInTheDocument();
	expect(within(row('InfluxDB')).getByText('alles übertragen')).toBeInTheDocument();
	expect(within(row('Planer')).getByText('Plan von 10:00 Uhr')).toBeInTheDocument();
	expect(within(row('Home Assistant')).getByText('MQTT noch nicht verbunden')).toHaveClass('bad');
	expect(within(row('Preise')).getByText('bis 09.10. 23:45')).not.toHaveClass('bad');
});

it('nennt die Wallbox in der Selbstprüfung beim Namen aus den Einstellungen', async () => {
	api.limits.mockResolvedValue(limits());
	showSystem(system());
	const checks = card('core');
	expect(await within(checks).findByText('EVCS-Modus: EVCS')).toBeInTheDocument();
});

it('nennt die Wallbox in der Aktorenliste beim Namen aus den Einstellungen', async () => {
	api.limits.mockResolvedValue(limits());
	showSystem(system());
	const table = card('Aktoren');
	expect(await within(table).findByRole('cell', { name: 'EVCS' })).toBeInTheDocument();
	expect(within(table).queryByText(EVCS)).toBeNull();
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

// --- Bedienung: Regelbetrieb, Live-Schalter, Quittierung ------------------------------------

const LIVE_BUTTON = 'Live schalten…';
const EVCS = 'EVCS „evcs“';
const TESLA = 'Tesla Model 3';
const PROTOCOL = 'Prüfprotokoll Teil 2 für die gewählten Aktoren ist bestanden.';

/** Alle Aktoren der Demo-Welt, mit `live_allowed` je Aktor überschrieben */
function actuatorsWith(
	changes: Record<string, Partial<CoreHealth['actuators'][number]>>
): CoreHealth['actuators'] {
	return demoHealth().actuators.map((actuator) => ({
		...actuator,
		live_allowed: true,
		...changes[actuator.id]
	}));
}

function dialog(): HTMLElement {
	return screen.getByRole('dialog');
}

/** Öffnet den Live-Dialog und füllt ihn bis auf die genannten Teile aus */
async function openLive() {
	await fireEvent.click(await screen.findByRole('button', { name: LIVE_BUTTON }));
	return within(dialog());
}

it('Admin schaltet EVCS und Tesla gemeinsam live', async () => {
	show({ actuators: actuatorsWith({}) });
	api.setActuators.mockResolvedValue(reply());
	const live = await openLive();
	await fireEvent.click(live.getByRole('checkbox', { name: EVCS }));
	await fireEvent.click(live.getByRole('checkbox', { name: TESLA }));
	await fireEvent.click(
		live.getByRole('checkbox', {
			name: 'Prüfprotokoll Teil 2 für die gewählten Aktoren ist bestanden.'
		})
	);
	await fireEvent.click(live.getByRole('checkbox', { name: 'evcc ist ganz abgeschaltet.' }));
	await fireEvent.input(live.getByLabelText('Passwort'), { target: { value: 'demo-passwort-1' } });
	const loaded = api.control.mock.calls.length;
	await fireEvent.click(live.getByRole('button', { name: 'Live schalten' }));
	await waitFor(() =>
		expect(api.setActuators).toHaveBeenCalledWith(
			{ 'wallbox.evcs': 'live', 'vehicle.tesla': 'live' },
			'demo-passwort-1',
			true
		)
	);
	await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
	await waitFor(() => expect(api.control.mock.calls.length).toBeGreaterThan(loaded)); // Absicht neu
});

it('zeigt im Live-Dialog Titel, Hinweis und die Bedingungen je Aktor', async () => {
	show({
		actuators: actuatorsWith({ victron: { locked: ['Dynamic ESS ist aktiv (Modus 1).'] } })
	});
	const live = await openLive();
	expect(live.getByRole('heading', { name: 'Live schalten' })).toBeInTheDocument();
	expect(live.getByText('EVCS und Tesla gehen gemeinsam live.')).toBeInTheDocument();
	const conditions = (name: string) =>
		listItems(following(live.getByRole('checkbox', { name }).closest('label')!));
	// Watchdog nur bei Victron und EVCS (watchdog_needed); der Watchdog der Demo ist nicht scharf
	expect(conditions('Victron (ESS)')).toEqual([
		'live erlaubt: ja',
		'Watchdog scharf: nein',
		'Regelbetrieb an: ja',
		'nicht gesperrt: nein'
	]);
	expect(conditions(TESLA)).toEqual([
		'live erlaubt: ja',
		'Regelbetrieb an: ja',
		'nicht gesperrt: ja'
	]);
});

it('ohne beide Häkchen bleibt „Live schalten“ gesperrt', async () => {
	show({ actuators: actuatorsWith({}) });
	const live = await openLive();
	const submit = live.getByRole('button', { name: 'Live schalten' });
	const protocol = () => live.getByRole('checkbox', { name: PROTOCOL });
	const evcc = () => live.getByRole('checkbox', { name: 'evcc ist ganz abgeschaltet.' });
	await fireEvent.input(live.getByLabelText('Passwort'), { target: { value: 'demo-passwort-1' } });
	expect(submit).toBeDisabled(); // kein Aktor gewählt
	expect(live.queryByRole('checkbox', { name: 'evcc ist ganz abgeschaltet.' })).toBeNull();
	await fireEvent.click(live.getByRole('checkbox', { name: 'Victron (ESS)' }));
	expect(submit).toBeDisabled(); // Prüfprotokoll fehlt
	await fireEvent.click(protocol());
	expect(submit).toBeEnabled(); // Victron braucht evcc nicht
	await fireEvent.click(protocol());
	expect(submit).toBeDisabled(); // das Prüfprotokoll fehlt wieder
	expect(api.setActuators).not.toHaveBeenCalled();
	await fireEvent.click(protocol());
	await fireEvent.click(live.getByRole('checkbox', { name: EVCS }));
	expect(evcc()).not.toBeChecked();
	await fireEvent.click(protocol());
	expect(submit).toBeDisabled(); // mit EVCS fehlt die zweite Bestätigung
	await fireEvent.click(evcc());
	expect(submit).toBeEnabled();
});

it('ändert sich die Auswahl, gelten die Bestätigungen nicht mehr', async () => {
	show({ actuators: actuatorsWith({}) });
	const live = await openLive();
	const submit = live.getByRole('button', { name: 'Live schalten' });
	const protocol = () => live.getByRole('checkbox', { name: PROTOCOL });
	const evcc = () => live.getByRole('checkbox', { name: 'evcc ist ganz abgeschaltet.' });
	await fireEvent.input(live.getByLabelText('Passwort'), { target: { value: 'demo-passwort-1' } });
	await fireEvent.click(live.getByRole('checkbox', { name: 'Victron (ESS)' }));
	await fireEvent.click(protocol());
	expect(submit).toBeEnabled();
	// ein Aktor kommt dazu: das Häkchen für die alte Auswahl trägt nicht auf die neue
	await fireEvent.click(live.getByRole('checkbox', { name: EVCS }));
	expect(protocol()).not.toBeChecked();
	expect(evcc()).not.toBeChecked();
	expect(submit).toBeDisabled();
	await fireEvent.click(protocol());
	await fireEvent.click(evcc());
	expect(submit).toBeEnabled();
	// ein Aktor fällt weg: wieder beide Häkchen neu
	await fireEvent.click(live.getByRole('checkbox', { name: 'Victron (ESS)' }));
	expect(protocol()).not.toBeChecked();
	expect(evcc()).not.toBeChecked();
	expect(submit).toBeDisabled();
	await fireEvent.click(protocol());
	await fireEvent.click(evcc());
	await fireEvent.click(live.getByRole('checkbox', { name: EVCS })); // keiner mehr gewählt
	await fireEvent.click(live.getByRole('checkbox', { name: EVCS }));
	expect(evcc()).not.toBeChecked();
	expect(protocol()).not.toBeChecked();
	expect(api.setActuators).not.toHaveBeenCalled();
});

it('Aktor ohne live_allowed ist nicht wählbar', async () => {
	show({ actuators: actuatorsWith({ victron: { live_allowed: false } }) });
	const live = await openLive();
	expect(live.getByRole('checkbox', { name: 'Victron (ESS)' })).toBeDisabled();
	expect(live.getByRole('checkbox', { name: EVCS })).toBeEnabled();
	const victron = following(
		live.getByRole('checkbox', { name: 'Victron (ESS)' }).closest('label')!
	);
	expect(listItems(victron)[0]).toBe('live erlaubt: nein');
});

it('falsches Passwort bleibt im Dialog', async () => {
	show({ actuators: actuatorsWith({}) });
	api.setActuators.mockRejectedValue(new ApiError(403, 'Passwort falsch.'));
	const live = await openLive();
	await fireEvent.click(live.getByRole('checkbox', { name: 'Victron (ESS)' }));
	await fireEvent.click(
		live.getByRole('checkbox', {
			name: 'Prüfprotokoll Teil 2 für die gewählten Aktoren ist bestanden.'
		})
	);
	await fireEvent.input(live.getByLabelText('Passwort'), { target: { value: 'falsch' } });
	await fireEvent.click(live.getByRole('button', { name: 'Live schalten' }));
	expect(await live.findByText('Passwort falsch.')).toBeInTheDocument();
	expect(dialog()).toBeInTheDocument();
	expect(live.getByLabelText('Passwort')).toHaveValue(''); // das falsche Passwort bleibt nicht stehen
});

it('Abbrechen schließt den Live-Dialog, ohne etwas zu ändern', async () => {
	show({ actuators: actuatorsWith({}) });
	const live = await openLive();
	await fireEvent.click(live.getByRole('checkbox', { name: 'Victron (ESS)' }));
	await fireEvent.click(live.getByRole('button', { name: 'Abbrechen' }));
	await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
	expect(api.setActuators).not.toHaveBeenCalled();
	// beim nächsten Öffnen ist nichts mehr gewählt
	const again = await openLive();
	expect(again.getByRole('checkbox', { name: 'Victron (ESS)' })).not.toBeChecked();
});

/**
 * Esc: der Browser sendet „cancel“ und schließt den Dialog („close“), wenn das nicht abgewiesen
 * wird. jsdom kennt `close()` nicht, darum wird es nachgestellt. `force` schließt auch nach einer
 * Abweisung (Chrome lässt das zweite Esc ohne Eingabe dazwischen zu). Liefert, ob „cancel“
 * abgewiesen wurde.
 */
function pressEscape({ force = false } = {}): boolean {
	const element = dialog() as HTMLDialogElement;
	const cancel = new Event('cancel', { cancelable: true });
	element.dispatchEvent(cancel);
	if (!cancel.defaultPrevented || force) {
		element.removeAttribute('open');
		element.dispatchEvent(new Event('close'));
	}
	return cancel.defaultPrevented;
}

it('Esc schließt den Live-Dialog, ohne etwas zu senden, und beim Öffnen ist alles leer', async () => {
	show({ actuators: actuatorsWith({}) });
	const live = await openLive();
	await fireEvent.click(live.getByRole('checkbox', { name: EVCS }));
	await fireEvent.click(live.getByRole('checkbox', { name: PROTOCOL }));
	await fireEvent.input(live.getByLabelText('Passwort'), { target: { value: 'demo-passwort-1' } });
	pressEscape();
	await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
	expect(api.setActuators).not.toHaveBeenCalled();
	const again = await openLive();
	expect(again.getByRole('checkbox', { name: EVCS })).not.toBeChecked();
	expect(again.getByRole('checkbox', { name: PROTOCOL })).not.toBeChecked();
	expect(again.getByLabelText('Passwort')).toHaveValue('');
	expect(again.queryByRole('checkbox', { name: 'evcc ist ganz abgeschaltet.' })).toBeNull();
});

it('während der Anfrage lässt sich der Live-Dialog nicht schließen', async () => {
	show({ actuators: actuatorsWith({}) });
	let finish: (value: ControlReply) => void = () => {};
	api.setActuators.mockReturnValue(new Promise<ControlReply>((resolve) => (finish = resolve)));
	const live = await openLive();
	await fireEvent.click(live.getByRole('checkbox', { name: 'Victron (ESS)' }));
	await fireEvent.click(live.getByRole('checkbox', { name: PROTOCOL }));
	await fireEvent.input(live.getByLabelText('Passwort'), { target: { value: 'demo-passwort-1' } });
	await fireEvent.click(live.getByRole('button', { name: 'Live schalten' }));
	await waitFor(() => expect(api.setActuators).toHaveBeenCalledTimes(1));
	expect(live.getByRole('button', { name: 'Abbrechen' })).toBeDisabled();
	expect(live.getByRole('button', { name: 'Schließen' })).toBeDisabled();
	expect(pressEscape()).toBe(true); // „cancel“ wird abgewiesen
	expect(pressEscape({ force: true })).toBe(true); // und ein erzwungenes Schließen öffnet wieder
	await waitFor(() => expect(screen.getByRole('dialog')).toBeInTheDocument());
	finish(reply());
	await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
	expect(api.setActuators).toHaveBeenCalledTimes(1);
});

it('während der Anfrage lässt sich der Dry-Run-Dialog nicht schließen', async () => {
	show({ actuators: actuatorsWith({ victron: { requested: 'live', effective: 'live' } }) });
	let finish: (value: ControlReply) => void = () => {};
	api.setActuators.mockReturnValue(new Promise<ControlReply>((resolve) => (finish = resolve)));
	await fireEvent.click(await screen.findByRole('button', { name: 'Auf Dry-Run…' }));
	const confirm = within(dialog());
	await fireEvent.click(confirm.getByRole('button', { name: 'Auf Dry-Run' }));
	await waitFor(() => expect(api.setActuators).toHaveBeenCalledTimes(1));
	expect(confirm.getByRole('button', { name: 'Abbrechen' })).toBeDisabled();
	expect(pressEscape()).toBe(true);
	expect(pressEscape({ force: true })).toBe(true);
	await waitFor(() => expect(screen.getByRole('dialog')).toBeInTheDocument());
	finish(reply());
	await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
});

it('Begrenzung durch den core steht nach dem Schalten als Warnung an der Karte', async () => {
	show({ actuators: actuatorsWith({}) });
	api.setActuators.mockResolvedValue(
		reply({ result: 'clamped', details: ['Victron (ESS) bleibt Dry-Run: nicht freigegeben.'] })
	);
	const live = await openLive();
	await fireEvent.click(live.getByRole('checkbox', { name: 'Victron (ESS)' }));
	await fireEvent.click(
		live.getByRole('checkbox', {
			name: 'Prüfprotokoll Teil 2 für die gewählten Aktoren ist bestanden.'
		})
	);
	await fireEvent.input(live.getByLabelText('Passwort'), { target: { value: 'demo-passwort-1' } });
	await fireEvent.click(live.getByRole('button', { name: 'Live schalten' }));
	expect(
		await within(card('Bedienung')).findByText('Victron (ESS) bleibt Dry-Run: nicht freigegeben.')
	).toBeInTheDocument();
});

it('Live- und Dry-Run-Dialog nennen die Wallbox beim Namen aus den Einstellungen', async () => {
	api.limits.mockResolvedValue(limits());
	show({
		actuators: actuatorsWith({ 'wallbox.evcs': { requested: 'live', effective: 'live' } })
	});
	await waitFor(() => expect(api.limits).toHaveBeenCalled());
	const live = await openLive();
	expect(await live.findByRole('checkbox', { name: 'EVCS' })).toBeInTheDocument();
	expect(live.queryByRole('checkbox', { name: EVCS })).toBeNull();
	await fireEvent.click(live.getByRole('button', { name: 'Abbrechen' }));
	await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
	await fireEvent.click(await screen.findByRole('button', { name: 'Auf Dry-Run…' }));
	expect(await within(dialog()).findByRole('checkbox', { name: 'EVCS' })).toBeInTheDocument();
});

it('Admin stellt die gewählten Live-Aktoren nach Bestätigung auf Dry-Run', async () => {
	show({
		actuators: actuatorsWith({
			'wallbox.evcs': { requested: 'live', effective: 'live' },
			'vehicle.tesla': { requested: 'live', effective: 'live' }
		})
	});
	api.setActuators.mockResolvedValue(reply());
	await fireEvent.click(await screen.findByRole('button', { name: 'Auf Dry-Run…' }));
	const confirm = within(dialog());
	expect(confirm.getByRole('heading', { name: 'Auf Dry-Run stellen' })).toBeInTheDocument();
	expect(
		confirm.getByText('Der core übergibt die gewählten Aktoren und setzt ihre Sicherwerte.')
	).toBeInTheDocument();
	expect(confirm.queryByLabelText('Passwort')).toBeNull(); // sicherer Zustand: ohne Passwort
	// nur die Aktoren, die jetzt live sind, alle vorgewählt
	expect(confirm.getAllByRole('checkbox').map((box) => (box as HTMLInputElement).checked)).toEqual([
		true,
		true
	]);
	expect(confirm.queryByRole('checkbox', { name: 'Victron (ESS)' })).toBeNull();
	await fireEvent.click(confirm.getByRole('checkbox', { name: TESLA }));
	await fireEvent.click(confirm.getByRole('button', { name: 'Auf Dry-Run' }));
	await waitFor(() =>
		expect(api.setActuators).toHaveBeenCalledWith({ 'wallbox.evcs': 'dry_run' }, null, false)
	);
	await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
});

it('Dry-Run: ohne gewählten Aktor ist die Taste gesperrt, jedes Öffnen wählt alle vor', async () => {
	show({
		actuators: actuatorsWith({
			'wallbox.evcs': { requested: 'live', effective: 'live' },
			victron: { requested: 'live', effective: 'live' }
		})
	});
	await fireEvent.click(await screen.findByRole('button', { name: 'Auf Dry-Run…' }));
	let confirm = within(dialog());
	const submit = confirm.getByRole('button', { name: 'Auf Dry-Run' });
	await fireEvent.click(confirm.getByRole('checkbox', { name: 'Victron (ESS)' }));
	expect(submit).toBeEnabled();
	await fireEvent.click(confirm.getByRole('checkbox', { name: EVCS }));
	expect(submit).toBeDisabled();
	await fireEvent.click(confirm.getByRole('button', { name: 'Abbrechen' }));
	await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
	expect(api.setActuators).not.toHaveBeenCalled();
	await fireEvent.click(screen.getByRole('button', { name: 'Auf Dry-Run…' }));
	confirm = within(dialog());
	expect(confirm.getAllByRole('checkbox').map((box) => (box as HTMLInputElement).checked)).toEqual([
		true,
		true
	]);
});

it('„Auf Dry-Run…“ ist gesperrt, solange kein Aktor live ist', async () => {
	show({ actuators: actuatorsWith({}) });
	expect(await screen.findByRole('button', { name: 'Auf Dry-Run…' })).toBeDisabled();
});

it('„Abbrechen“ am Dry-Run-Dialog ändert nichts', async () => {
	show({ actuators: actuatorsWith({ victron: { requested: 'live', effective: 'live' } }) });
	await fireEvent.click(await screen.findByRole('button', { name: 'Auf Dry-Run…' }));
	await fireEvent.click(within(dialog()).getByRole('button', { name: 'Abbrechen' }));
	await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
	expect(api.setActuators).not.toHaveBeenCalled();
});

it('Bedienen schaltet den Regelbetrieb, sieht aber keinen Live-Schalter', async () => {
	user.role = 'operator';
	show({});
	api.setRegulation.mockResolvedValue(reply());
	await screen.findByRole('heading', { level: 2, name: 'Bedienung' });
	const control = within(card('Bedienung'));
	const toggle = await control.findByRole('switch', { name: 'Regelbetrieb' });
	expect(toggle).toBeChecked(); // Absicht der Demo: Regelbetrieb an
	expect(control.queryByRole('button', { name: LIVE_BUTTON })).toBeNull();
	expect(control.queryByRole('button', { name: 'Auf Dry-Run…' })).toBeNull();
	await fireEvent.click(toggle);
	await waitFor(() => expect(api.setRegulation).toHaveBeenCalledWith(false));
	expect(screen.queryByRole('button', { name: 'Quittieren' })).toBeNull();
});

it('Regelbetrieb: Lesen sieht den Stand ohne Schalter', async () => {
	user.role = 'viewer';
	show({});
	await screen.findByRole('heading', { level: 2, name: 'Bedienung' });
	const control = within(card('Bedienung'));
	await control.findByText('Regelbetrieb');
	expect(control.queryByRole('switch')).toBeNull();
	expect(fact(card('Bedienung'), 'Regelbetrieb')).toHaveTextContent(/^an$/);
});

it('Regelbetrieb: Ablehnung steht an der Karte, der Schalter springt zurück', async () => {
	api.control.mockResolvedValue({ core_connected: true, intent: baseIntent({ control: false }) });
	show({});
	api.setRegulation.mockRejectedValue(new ApiError(503, 'core nicht erreichbar.'));
	await screen.findByRole('heading', { level: 2, name: 'Bedienung' });
	const toggle = await within(card('Bedienung')).findByRole('switch', { name: 'Regelbetrieb' });
	await waitFor(() => expect(toggle).not.toBeChecked());
	await fireEvent.click(toggle);
	expect(await within(card('Bedienung')).findByText('core nicht erreichbar.')).toBeInTheDocument();
	expect(toggle).not.toBeChecked();
});

it('Quittieren zeigt die Antwort des core', async () => {
	const locked = ['Dynamic ESS ist aktiv (Modus 1).'];
	show({ actuators: actuatorsWith({ 'wallbox.evcs': { locked } }) });
	api.acknowledge.mockRejectedValue(
		new ApiError(409, 'Ursache besteht weiter: Dynamic ESS ist aktiv (Modus 1).')
	);
	await fireEvent.click(await screen.findByRole('button', { name: 'Quittieren' }));
	await waitFor(() => expect(api.acknowledge).toHaveBeenCalledWith('wallbox.evcs'));
	expect(
		await within(card('Aktoren')).findByText(
			'Ursache besteht weiter: Dynamic ESS ist aktiv (Modus 1).'
		)
	).toBeInTheDocument();
});

it('Quittieren: die Annahme steht als Hinweis, die Aktoren laden neu', async () => {
	show({ actuators: actuatorsWith({ victron: { locked: ['Die Ursache fehlt.'] } }) });
	api.acknowledge.mockResolvedValue({
		detail: 'Quittiert. Die Sperre von Victron (ESS) löst sich im nächsten Zyklus.'
	});
	const loaded = api.system.mock.calls.length;
	await fireEvent.click(await screen.findByRole('button', { name: 'Quittieren' }));
	expect(
		await within(card('Aktoren')).findByText(
			'Quittiert. Die Sperre von Victron (ESS) löst sich im nächsten Zyklus.'
		)
	).toBeInTheDocument();
	await waitFor(() => expect(api.system.mock.calls.length).toBeGreaterThan(loaded));
});

it('Quittieren fehlt für Admins, solange kein Aktor gesperrt ist', async () => {
	show({ actuators: actuatorsWith({}) });
	await screen.findByRole('button', { name: LIVE_BUTTON });
	expect(screen.queryByRole('button', { name: 'Quittieren' })).toBeNull(); // nichts gesperrt
});

it('Bedienen sieht bei einer Sperre kein „Quittieren“', async () => {
	user.role = 'operator';
	show({ actuators: actuatorsWith({ victron: { locked: ['Die Ursache fehlt.'] } }) });
	await within(card('Aktoren')).findByText('gesperrt: Die Ursache fehlt.');
	expect(screen.queryByRole('button', { name: 'Quittieren' })).toBeNull();
});

it('Watchdog: die Zeile „Scharf“ zeigt ja oder nein', async () => {
	show({ watchdog: watchdogWith({ armed: true }) });
	expect(await within(card('Watchdog am Cerbo')).findByText('Scharf')).toBeInTheDocument();
	expect(fact(card('Watchdog am Cerbo'), 'Scharf')).toHaveTextContent(/^ja$/);
});

it('Watchdog: ohne scharfen Watchdog steht „nein“', async () => {
	showSystem(system());
	await within(card('Watchdog am Cerbo')).findByText('Scharf');
	expect(fact(card('Watchdog am Cerbo'), 'Scharf')).toHaveTextContent(/^nein$/);
});

it('Karte „Bedienung“ steht vor der Karte „Aktoren“', async () => {
	showSystem(system());
	await within(card('Aktoren')).findByRole('table');
	expect(
		card('Bedienung').compareDocumentPosition(card('Aktoren')) & Node.DOCUMENT_POSITION_FOLLOWING
	).toBeTruthy();
});
