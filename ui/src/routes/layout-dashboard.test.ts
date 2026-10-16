import { render, screen, within } from '@testing-library/svelte';
import { beforeEach, expect, it, vi } from 'vitest';
import { latestDecisions, system } from '$lib/testing/fixtures';
import Page from './+page.svelte';

const { api } = vi.hoisted(() => ({
	api: {
		system: vi.fn(),
		consumers: vi.fn(),
		limits: vi.fn(),
		pricesNow: vi.fn(),
		prices: vi.fn(),
		forecast: vi.fn(),
		latestDecisions: vi.fn()
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
	for (const name of ['consumers', 'limits', 'pricesNow', 'prices', 'forecast'] as const) {
		api[name].mockReturnValue(new Promise(() => {}));
	}
	api.system.mockResolvedValue(system());
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
	expect(await card.findByText(decisions[0]!.text)).toBeInTheDocument();
	expect(card.getByText(decisions[1]!.text)).toBeInTheDocument();
	expect(card.queryByText(decisions[2]!.text)).not.toBeInTheDocument();
	// Aktor mit Namen aus Health, Verweis auf die gefilterte Liste
	expect(await card.findByRole('link', { name: 'Victron (ESS)' })).toHaveAttribute(
		'href',
		'/entscheidungen?actuator=victron'
	);
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
