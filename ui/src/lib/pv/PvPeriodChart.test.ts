import { fireEvent, render, screen, within } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';
import type { PvSource } from '$lib/api/schemas';
import { readPalette } from '$lib/charts/palette';
import { HUAWEI, SUED, WEST, pvWeek, pvYear } from '$lib/testing/pv';
import { noDataRange, periodEntries, periodOption } from './charts';
import PvPeriodChart from './PvPeriodChart.svelte';

vi.mock('$lib/charts/echarts', () => ({ loadECharts: () => new Promise(() => {}) }));

const BALKON: PvSource = { ...HUAWEI, id: 4, name: 'Balkon', kwp: null, sort: 1 };

interface Series {
	name: string;
	data: (number | null)[];
	markArea?: { data: [{ xAxis: number; name: string }, { xAxis: number }][] };
}

function option(period = pvYear(), sources = [HUAWEI, SUED, WEST], unit: 'kwh' | 'kwp' = 'kwh') {
	const { shown } = periodEntries(sources, 'systems', unit);
	return periodOption(period, shown, unit, '2026-11-08', readPalette()) as {
		xAxis: { data: string[] };
		series: Series[];
	};
}

function chips(): string[] {
	const group = screen.getByRole('group', { name: /^Reihen von/ });
	return within(group)
		.getAllByRole('button')
		.map((button) => button.textContent?.trim() ?? '');
}

describe('periodOption', () => {
	it('hat im Jahr 12 Kategorien von Jänner bis Dezember', () => {
		const { xAxis } = option();
		expect(xAxis.data).toHaveLength(12);
		expect(xAxis.data[0]).toBe('Jän.');
		expect(xAxis.data[11]).toBe('Dez.');
	});

	it('lässt einen Monat ohne Daten leer statt einen 0-Balken zu zeichnen', () => {
		const [huawei] = option().series;
		expect(huawei?.data.slice(0, 8)).toEqual([null, null, null, null, null, null, null, 12]);
		expect(huawei?.data[11]).toBeNull();
	});

	it('hinterlegt die Monate vor den ersten Werten grau', () => {
		const [huawei] = option().series;
		expect(huawei?.markArea?.data).toEqual([
			[{ xAxis: 0, name: 'noch keine Daten (Werte ab 30.08.)' }, { xAxis: 6 }]
		]);
		expect(noDataRange(pvWeek(), [HUAWEI], '2026-11-08')).toBeNull();
	});

	it('rechnet kWh/kWp je Balken', () => {
		const [huawei] = option(pvWeek(), [HUAWEI], 'kwp').series;
		expect(huawei?.data[0]).toBeCloseTo(7 / 9.8, 3);
	});

	it('beschriftet die Woche mit Wochentagen und den Monat mit dem Tag', () => {
		expect(option(pvWeek()).xAxis.data).toEqual(['Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa', 'So']);
	});
});

describe('PvPeriodChart', () => {
	it('zeigt Anlagen, auf „Strings“ die Strings je kWp', async () => {
		render(PvPeriodChart, {
			period: pvWeek(),
			sources: [HUAWEI, SUED, WEST],
			today: '2026-11-08'
		});
		expect(screen.getByRole('heading', { name: 'Je Tag' })).toBeInTheDocument();
		expect(chips()).toEqual(['Huawei']);
		await fireEvent.click(screen.getByRole('button', { name: 'Strings' }));
		expect(chips()).toEqual(['Süd', 'West']);
		expect(screen.getByRole('button', { name: 'kWh/kWp' })).toHaveAttribute('aria-pressed', 'true');
	});

	it('blendet bei kWh/kWp Einträge ohne kWp aus und sagt das', async () => {
		render(PvPeriodChart, {
			period: { ...pvWeek(), sources: [...pvWeek().sources, { ...pvWeek().sources[0]!, id: 4 }] },
			sources: [HUAWEI, BALKON],
			today: '2026-11-08'
		});
		expect(chips()).toEqual(['Huawei', 'Balkon']);
		expect(screen.queryByText(/ohne kWp/)).not.toBeInTheDocument();
		await fireEvent.click(screen.getByRole('button', { name: 'kWh/kWp' }));
		expect(chips()).toEqual(['Huawei']);
		expect(screen.getByText('ohne kWp: Balkon')).toBeInTheDocument();
	});

	it('nennt die Werte je Monat in einer Tabelle für Screenreader', () => {
		render(PvPeriodChart, { period: pvYear(), sources: [HUAWEI], today: '2026-11-08' });
		expect(screen.getByRole('heading', { name: 'Je Monat' })).toBeInTheDocument();
		const table = screen.getByRole('table', { name: 'Ertrag je Monat' });
		const rows = within(table).getAllByRole('row').slice(1);
		expect(rows).toHaveLength(12);
		expect(rows[0]?.textContent).toContain('–');
		expect(rows[8]?.textContent).toContain('260,0');
	});
});
