import { render, screen, within } from '@testing-library/svelte';
import type { LineSeriesOption } from 'echarts/charts';
import { describe, expect, it, vi } from 'vitest';
import { ApiError } from '$lib/api/errors';
import type {
	HistoryResponse,
	PlanReview as PlanReviewData,
	ReviewActual,
	ReviewPlanned
} from '$lib/api/schemas';
import type { Palette } from '$lib/charts/palette';
import { NBSP } from '$lib/format';
import { iso } from '$lib/testing/plan';
import PlanReview from './PlanReview.svelte';
import { hasComparison, reviewOption, reviewTotals } from './review';

// ECharts wird nicht geladen: das Diagramm bleibt leer, die Karte darum herum ist das Thema
vi.mock('$lib/charts/echarts', () => ({ loadECharts: () => new Promise(() => {}) }));

const palette: Palette = {
	text: '#111',
	muted: '#666',
	grid: '#ddd',
	importPrice: '#c00',
	feedIn: '#090',
	pv: '#fa0',
	load: '#80f',
	series: ['#00f', '#0a0', '#a0a']
};

const SLOT_MS = 900_000;

function planned(socEnd: number, changes: Partial<ReviewPlanned> = {}): ReviewPlanned {
	return {
		intent: 'EIGENVERBRAUCH',
		grid_charge_w: 0,
		soc_end_pct: socEnd,
		import_w: 400,
		export_w: 0,
		battery_w: 1000,
		import_cost_ct: 3,
		plan_id: 1,
		source: 'optimizer',
		...changes
	};
}

function actual(changes: Partial<ReviewActual> = {}): ReviewActual {
	return {
		import_w: 800,
		export_w: 100,
		battery_w: 900,
		grid_charge_w: 0,
		import_cost_ct: 5,
		...changes
	};
}

function review(
	slots: PlanReviewData['slots'],
	totals: Partial<PlanReviewData['totals']> = {}
): PlanReviewData {
	return {
		day: '2026-10-09',
		slots,
		totals: {
			planned: { import_kwh: 1.234, export_kwh: 0.5, grid_charge_kwh: 0, import_cost_eur: 0.1 },
			actual: { import_kwh: 2.5, export_kwh: 0.25, grid_charge_kwh: 1.2, import_cost_eur: 0.62 },
			...totals
		}
	};
}

const BOTH = review([
	{ start: iso('09:00'), planned: planned(50), actual: actual() },
	{ start: iso('09:15'), planned: planned(52), actual: actual() },
	{ start: iso('09:30'), planned: null, actual: actual() },
	{ start: iso('09:45'), planned: planned(55), actual: null }
]);

const EMPTY = review([
	{ start: iso('09:00'), planned: planned(50), actual: null },
	{ start: iso('09:15'), planned: null, actual: actual() }
]);

const SOC: HistoryResponse = {
	rp: 'raw',
	step_s: 900,
	series: {
		'soc.battery': {
			label: 'Akku-SOC',
			unit: '%',
			points: [
				[Date.parse(iso('08:45')), 48],
				[Date.parse(iso('09:00')), 50],
				[Date.parse(iso('09:15')), 51.5],
				[Date.parse(iso('10:15')), 60]
			]
		}
	}
};

/** Die Anzeige trennt Zahl und Einheit mit geschütztem Leerzeichen */
const nb = (text: string): string => text.replaceAll(' ', NBSP);

describe('reviewTotals', () => {
	it('computes the difference as actual minus plan and rounds like format.ts', () => {
		const rows = reviewTotals(BOTH);
		expect(rows.map((row) => row.label)).toEqual([
			'Netzbezug',
			'Einspeisung',
			'Netzladen',
			'Bezugskosten'
		]);
		// 2,5 − 1,234 = 1,266 kWh
		expect(rows[0]).toMatchObject({
			plan: nb('1,23 kWh'),
			actual: nb('2,50 kWh'),
			diff: nb('1,27 kWh')
		});
		// 0,25 − 0,5 = −0,25 kWh
		expect(rows[1]?.diff).toBe(nb('-0,25 kWh'));
		expect(rows[2]).toMatchObject({
			plan: nb('0,00 kWh'),
			actual: nb('1,20 kWh'),
			diff: nb('1,20 kWh')
		});
		// 0,62 − 0,10 = 0,52 €
		expect(rows[3]).toMatchObject({ plan: nb('€ 0,10'), actual: nb('€ 0,62'), diff: nb('€ 0,52') });
	});

	it('shows a difference that rounds to zero without a minus', () => {
		const rows = reviewTotals(
			review(BOTH.slots, {
				planned: { import_kwh: 1.5, export_kwh: 0.5, grid_charge_kwh: 0, import_cost_eur: 0.622 },
				actual: { import_kwh: 1.498, export_kwh: 0.5, grid_charge_kwh: 0, import_cost_eur: 0.62 }
			})
		);
		expect(rows[0]?.diff).toBe(nb('0,00 kWh'));
		expect(rows[3]?.diff).toBe(nb('€ 0,00'));
	});

	it('shows a dash for unknown costs and no difference', () => {
		const rows = reviewTotals(
			review(BOTH.slots, {
				actual: { import_kwh: 2.5, export_kwh: 0.25, grid_charge_kwh: 1.2, import_cost_eur: null }
			})
		);
		expect(rows[3]).toMatchObject({ plan: nb('€ 0,10'), actual: '–', diff: '–' });
	});
});

describe('hasComparison', () => {
	it('needs a quarter-hour with plan and actual', () => {
		expect(hasComparison(BOTH)).toBe(true);
		expect(hasComparison(EMPTY)).toBe(false);
		expect(hasComparison(review([]))).toBe(false);
	});
});

describe('reviewOption', () => {
	type Data = [number, number | null][];
	const series = (option: unknown) => (option as { series: LineSeriesOption[] }).series;

	it('has the series planned and actual SOC', () => {
		const result = series(reviewOption(BOTH, SOC, palette));
		expect(result.map((s) => s.name)).toEqual(['Geplanter SOC', 'Ist-SOC']);
	});

	it('puts the planned SOC at the end of each quarter-hour and breaks the line at gaps', () => {
		const planned = series(reviewOption(BOTH, SOC, palette))[0]?.data as Data;
		expect(planned).toEqual([
			[Date.parse(iso('09:15')), 50],
			[Date.parse(iso('09:30')), 52],
			[Date.parse(iso('09:45')), null],
			[Date.parse(iso('10:00')), 55]
		]);
	});

	it('draws the actual SOC from the history, only inside the span of the plan', () => {
		const result = series(reviewOption(BOTH, SOC, palette))[1]?.data as Data;
		expect(result).toEqual([
			[Date.parse(iso('09:00')), 50],
			[Date.parse(iso('09:15')), 51.5]
		]);
	});

	it('keeps the planned SOC without history', () => {
		const result = series(reviewOption(BOTH, undefined, palette));
		expect(result.map((s) => s.name)).toEqual(['Geplanter SOC', 'Ist-SOC']);
		expect(result[1]?.data).toEqual([]);
	});

	it('spans the time axis from the first planned quarter-hour to the end of the last', () => {
		const axis = (reviewOption(BOTH, SOC, palette) as { xAxis: { min: number; max: number } })
			.xAxis;
		expect(axis.min).toBe(Date.parse(iso('09:00')));
		expect(axis.max).toBe(Date.parse(iso('09:45')) + SLOT_MS);
	});
});

describe('Karte Plan gegen Ist (heute)', () => {
	it('shows the chart and the table with the totals', () => {
		render(PlanReview, { review: BOTH, soc: SOC, error: null, palette });
		const card = screen
			.getByRole('heading', { name: 'Plan gegen Ist (heute)' })
			.closest('section')!;
		expect(screen.getByRole('img', { name: 'Plan gegen Ist (heute)' })).toBeTruthy();
		const table = within(card).getByRole('table');
		expect(
			within(table)
				.getAllByRole('columnheader')
				.map((cell) => cell.textContent)
		).toEqual(['Größe', 'Plan', 'Ist', 'Differenz']);
		const rows = within(table).getAllByRole('row');
		expect(rows).toHaveLength(5);
		expect(within(rows[1]!).getByText('Netzbezug')).toBeTruthy();
		expect(within(rows[1]!).getByText('1,27 kWh')).toBeTruthy();
		expect(within(rows[4]!).getByText('€ 0,52')).toBeTruthy();
	});

	it('says so when no quarter-hour has plan and actual', () => {
		render(PlanReview, { review: EMPTY, soc: SOC, error: null, palette });
		expect(
			screen.getByText('Für heute gibt es noch keine Viertelstunde mit Plan und Ist.')
		).toBeTruthy();
		expect(screen.queryByRole('table')).toBeNull();
		expect(screen.queryByRole('img')).toBeNull();
	});

	it('shows the error text instead of the server message', () => {
		render(PlanReview, {
			review: undefined,
			soc: undefined,
			error: new ApiError(500, 'Interner Fehler'),
			palette
		});
		expect(screen.getByText('Plan gegen Ist ist nicht verfügbar.')).toBeTruthy();
		expect(screen.queryByText('Interner Fehler')).toBeNull();
	});

	it('keeps the last data when a later load fails', () => {
		render(PlanReview, { review: BOTH, soc: SOC, error: new ApiError(500, 'x'), palette });
		expect(screen.getByText('Plan gegen Ist ist nicht verfügbar.')).toBeTruthy();
		expect(screen.getByRole('table')).toBeTruthy();
	});

	it('shows "Lädt …" until the first answer', () => {
		render(PlanReview, { review: undefined, soc: undefined, error: null, palette });
		expect(screen.getByText('Lädt …')).toBeTruthy();
	});
});
