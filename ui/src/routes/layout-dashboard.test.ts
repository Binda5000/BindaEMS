import { fireEvent, render, screen, within } from '@testing-library/svelte';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { formatPower, formatSoc } from '$lib/format';
import { live } from '$lib/live.svelte';
import {
	coreState,
	history,
	latestDecisions,
	limits,
	statsDay,
	system
} from '$lib/testing/fixtures';
import { RESPONSE as PLAN_RESPONSE } from '$lib/testing/plan';
import Page from './+page.svelte';

const { api } = vi.hoisted(() => ({
	api: {
		system: vi.fn(),
		consumers: vi.fn(),
		limits: vi.fn(),
		pricesNow: vi.fn(),
		prices: vi.fn(),
		forecast: vi.fn(),
		loadForecast: vi.fn(),
		latestDecisions: vi.fn(),
		plan: vi.fn(),
		history: vi.fn(),
		stats: vi.fn()
	}
}));
vi.mock('$lib/api/endpoints', async (original) => ({
	...(await original<typeof import('$lib/api/endpoints')>()),
	api
}));
// ECharts wird nicht geladen: das Diagramm bleibt leer, die Karten darum herum sind das Thema
vi.mock('$lib/charts/echarts', () => ({ loadECharts: () => new Promise(() => {}) }));

beforeEach(() => {
	vi.resetAllMocks();
	for (const name of [
		'consumers',
		'limits',
		'pricesNow',
		'prices',
		'forecast',
		'loadForecast',
		'plan',
		'history',
		'stats'
	] as const) {
		api[name].mockReturnValue(new Promise(() => {}));
	}
	api.system.mockResolvedValue(system());
});

afterEach(() => {
	live.state = null;
	vi.useRealTimers();
});

function why(): HTMLElement {
	const section = screen.getByRole('heading', { level: 2, name: 'Warum?' }).closest('section');
	if (section === null) throw new Error('Karte „Warum?“ fehlt');
	return section;
}

it('Warum? nennt je Aktor die jüngste Entscheidung', async () => {
	const { decisions } = latestDecisions();
	api.latestDecisions.mockResolvedValue({ decisions: decisions.slice(0, 2) });
	render(Page);
	const card = within(why());
	// Der Eintrag zeigt Name, Gründe mit Befehl und das Ergebnis als Etikett
	const items = await card.findAllByRole('listitem');
	expect(items).toHaveLength(2);
	expect(items[0]).toHaveTextContent('Überschuss: Netz-Sollwert 50 W');
	expect(items[1]).toHaveTextContent('Strom 8 A');
	expect(items[1]).not.toHaveTextContent('nicht gesendet');
	expect(within(items[0]!).getByText('Dry-Run')).toBeInTheDocument();
	expect(card.queryByText(/zurückgestellt/)).not.toBeInTheDocument();
	// Aktor mit Namen aus Health, die ganze Zeile verweist auf die gefilterte Liste
	expect(within(items[0]!).getByRole('link')).toHaveAttribute(
		'href',
		'/entscheidungen?actuator=victron'
	);
	expect(await card.findByText('Victron (ESS)')).toBeInTheDocument();
	expect(card.getByRole('link', { name: 'Alle Entscheidungen' })).toHaveAttribute(
		'href',
		'/entscheidungen'
	);
	expect(api.latestDecisions).toHaveBeenCalledTimes(1);
});

it('Warum? zeigt das Alter der Entscheidung', async () => {
	vi.useFakeTimers({ toFake: ['Date'] });
	vi.setSystemTime(new Date('2026-10-09T08:00:00Z')); // 3 min nach der ersten Entscheidung
	api.latestDecisions.mockResolvedValue({ decisions: latestDecisions().decisions.slice(0, 1) });
	try {
		render(Page);
		expect(await within(why()).findByText('vor 3 min')).toBeInTheDocument();
	} finally {
		vi.useRealTimers();
	}
});

it('Warum? sagt, wenn es in 24 h nichts zu erklären gibt', async () => {
	api.latestDecisions.mockResolvedValue({ decisions: [] });
	render(Page);
	const card = within(why());
	expect(await card.findByText('Keine Entscheidungen in den letzten 24 h.')).toBeInTheDocument();
	expect(card.getByRole('link', { name: 'Alle Entscheidungen' })).toBeInTheDocument();
});

it('Warum? meldet einen Fehler und behält den Verweis', async () => {
	const { ApiError } = await import('$lib/api/errors');
	api.latestDecisions.mockRejectedValue(new ApiError(503, 'core nicht erreichbar.'));
	render(Page);
	const card = within(why());
	expect(await card.findByText('core nicht erreichbar.')).toBeInTheDocument();
	expect(card.getByRole('link', { name: 'Alle Entscheidungen' })).toBeInTheDocument();
});

it('das Diagramm bleibt, wenn die Lastprognose scheitert, und nennt den Fehler', async () => {
	const { ApiError } = await import('$lib/api/errors');
	api.latestDecisions.mockResolvedValue({ decisions: [] });
	api.loadForecast.mockRejectedValue(new ApiError(503, 'Lastprognose nicht erreichbar.'));
	render(Page);
	const chart = screen
		.getByRole('heading', { level: 2, name: 'Preise, PV und Akku heute/morgen' })
		.closest('section');
	if (chart === null) throw new Error('Karte „Preise, PV und Akku heute/morgen“ fehlt');
	expect(await within(chart).findByText('Lastprognose nicht erreichbar.')).toBeInTheDocument();
	expect(within(chart).getByRole('img', { name: /^Diagramm:/ })).toBeInTheDocument();
});

it('das Diagramm führt seine Reihen als Chips darunter, ein Tipp blendet sie aus', async () => {
	api.latestDecisions.mockResolvedValue({ decisions: [] });
	render(Page);
	const chart = screen
		.getByRole('heading', { level: 2, name: 'Preise, PV und Akku heute/morgen' })
		.closest('section');
	if (chart === null) throw new Error('Karte „Preise, PV und Akku heute/morgen“ fehlt');
	const group = await within(chart).findByRole('group', {
		name: /^Reihen von Diagramm: .* ein- und ausblenden$/
	});
	const price = within(group).getByRole('button', { name: 'Preis' });
	expect(price).toHaveAttribute('aria-pressed', 'true');
	await fireEvent.click(price);
	expect(price).toHaveAttribute('aria-pressed', 'false');
});

// --- Kennzahlen-Leiste ----------------------------------------------------------------------

/** Demo-Uhr: 09.10.2026, 10:00 in Wien – dazu passen Plan und Verlauf der Vertragsdateien */
const DEMO_NOW = new Date('2026-10-09T08:00:00Z');

/** Verlauf mit allen vier Reihen der Kacheln (PV und Haus aus dem Netz-Verlauf abgeleitet) */
function fullHistory() {
	const base = history();
	const grid = base.series['grid']!;
	return {
		...base,
		series: {
			...base.series,
			pv: { ...grid, label: 'PV', points: grid.points.map(([at, w]) => [at, w + 1000]) },
			house: { ...grid, label: 'Haus', points: grid.points.map(([at, w]) => [at, w + 500]) }
		}
	};
}

function liveData() {
	vi.useFakeTimers({ toFake: ['Date'] });
	vi.setSystemTime(DEMO_NOW);
	live.state = coreState();
	api.latestDecisions.mockResolvedValue({ decisions: [] });
	api.limits.mockResolvedValue(limits());
	api.plan.mockResolvedValue(PLAN_RESPONSE);
	api.history.mockResolvedValue(fullHistory());
	api.stats.mockResolvedValue(statsDay());
}

function kpi(id: 'pv' | 'house' | 'grid'): HTMLElement {
	return screen.getByTestId(`kpi-${id}`);
}

it('zeigt Akku, Kennzahlen, Woher–Wohin, Autos und Warum?', async () => {
	liveData();
	const state = coreState();
	render(Page);

	const battery = screen.getByRole('region', { name: 'Hausakku' });
	// formatSoc/formatPower setzen ein geschütztes Leerzeichen, das toHaveTextContent einebnet
	expect(within(battery).getByTestId('battery-soc').textContent).toBe(
		formatSoc(state.signals['battery.soc_pct']!.v as number)
	);
	expect(await within(battery).findByText(/^Plan: /)).toBeInTheDocument();

	expect(kpi('pv').textContent).toContain(formatPower(state.derived.pv_total_w));
	expect(kpi('house').textContent).toContain(formatPower(state.derived.house_load_w));
	expect(kpi('grid')).toHaveTextContent(/^Netz/);
	// Tageswerte und Mini-Linien
	expect(await within(kpi('pv')).findByText(/^heute /)).toBeInTheDocument();
	expect(within(kpi('grid')).getByText(/^heute ↓ .* · ↑ /)).toBeInTheDocument();
	for (const id of ['pv', 'house', 'grid'] as const) {
		expect(kpi(id).querySelector('polyline')).not.toBeNull();
	}
	expect(screen.getByTestId('price-now')).toBeInTheDocument();

	for (const title of ['Woher kommt der Strom – wohin geht er?', 'Autos', 'Warum?']) {
		expect(screen.getByRole('heading', { level: 2, name: title })).toBeInTheDocument();
	}
	// Autos aus den Grenzen der Anlage
	const cars = screen.getByRole('heading', { level: 2, name: 'Autos' }).closest('section');
	if (cars === null) throw new Error('Karte „Autos“ fehlt');
	const [firstCar] = Object.values(limits().vehicles);
	expect(await within(cars).findByText(firstCar!.name)).toBeInTheDocument();
});

it('fragt Plan, Verlauf ab 0 Uhr in Wien bis jetzt und Tageswerte ab', async () => {
	liveData();
	render(Page);
	await screen.findByText(/^Plan: /);
	expect(api.plan).toHaveBeenCalledTimes(1);
	expect(api.history).toHaveBeenCalledWith(
		['pv', 'house', 'grid', 'soc.battery'],
		{ from: '2026-10-08T22:00:00.000Z', to: '2026-10-09T08:00:00.000Z' },
		expect.anything()
	);
	expect(api.stats).toHaveBeenCalledWith('day', undefined, expect.anything());
});

it('scheitert der Plan, fehlt nur die Planzeile', async () => {
	const { ApiError } = await import('$lib/api/errors');
	liveData();
	api.plan.mockRejectedValue(new ApiError(503, 'Planer nicht erreichbar.'));
	render(Page);
	expect(await within(kpi('pv')).findByText(/^heute /)).toBeInTheDocument();
	await vi.waitFor(() => expect(kpi('pv').querySelector('polyline')).not.toBeNull());
	const battery = screen.getByRole('region', { name: 'Hausakku' });
	expect(within(battery).getByTestId('battery-soc')).toHaveTextContent('%');
	expect(within(battery).queryByText(/^Plan: /)).not.toBeInTheDocument();
	expect(screen.queryByText('Planer nicht erreichbar.')).not.toBeInTheDocument();
});

it('scheitert der Verlauf, fehlen nur die Mini-Linien', async () => {
	const { ApiError } = await import('$lib/api/errors');
	liveData();
	api.history.mockRejectedValue(new ApiError(503, 'Verlauf nicht erreichbar.'));
	render(Page);
	expect(await screen.findByText(/^Plan: /)).toBeInTheDocument();
	expect(await within(kpi('pv')).findByText(/^heute /)).toBeInTheDocument();
	for (const id of ['pv', 'house', 'grid'] as const) {
		expect(kpi(id)).toHaveTextContent(/\d/);
		expect(kpi(id).querySelector('polyline')).toBeNull();
	}
	expect(screen.queryByText('Verlauf nicht erreichbar.')).not.toBeInTheDocument();
});

it('scheitern die Tageswerte, fehlt nur der Tageswert', async () => {
	const { ApiError } = await import('$lib/api/errors');
	liveData();
	api.stats.mockRejectedValue(new ApiError(503, 'Statistik nicht erreichbar.'));
	render(Page);
	await vi.waitFor(() => expect(kpi('pv').querySelector('polyline')).not.toBeNull());
	for (const id of ['pv', 'house', 'grid'] as const) {
		expect(kpi(id)).not.toHaveTextContent('heute');
	}
});

it('zeigt die Hinweis-Leiste über den Kacheln', async () => {
	api.latestDecisions.mockResolvedValue({ decisions: [] });
	render(Page);
	const bar = await screen.findByRole('region', { name: 'Hinweise' });
	expect(within(bar).getByRole('button', { name: 'Anzeigen' })).toBeInTheDocument();
	expect(
		bar.compareDocumentPosition(screen.getByRole('region', { name: 'Hausakku' })) &
			Node.DOCUMENT_POSITION_FOLLOWING
	).toBeTruthy();
});
