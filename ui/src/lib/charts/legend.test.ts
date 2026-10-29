import { describe, expect, it } from 'vitest';
import type { LoadForecast, PriceSlot } from '$lib/api/schemas';
import { NOW, PLAN, planWithWindows } from '$lib/testing/plan';
import { planSocOption } from '$lib/plan/chart';
import { legendItems, withSelection } from './legend';
import { priceForecastOption, statsEnergyOption } from './options';
import type { Palette } from './palette';
import { stats } from '$lib/testing/fixtures';

const palette: Palette = {
	text: '#111',
	muted: '#666',
	grid: '#ddd',
	importPrice: '#c00',
	feedIn: '#090',
	pv: '#fa0',
	load: '#80f',
	series: ['#00f', '#0a0']
};

const price: PriceSlot = {
	start: '2026-10-09T08:00:00+00:00',
	spot_net_ct: 9,
	import_net_ct: 11,
	import_gross_ct: 13.44,
	feed_in_ct: 7.3,
	origin: 'primary',
	missing: []
};
const forecast = [{ start: '2026-10-09T08:00:00+00:00', p50_w: 2500, p25_w: 1750, p75_w: 3000 }];
const load: LoadForecast = {
	issued_at: '2026-10-09T08:00:00+00:00',
	slots: [{ start: '2026-10-09T08:00:00+00:00', p50_w: 900 }],
	days: [],
	method: 'profile',
	basis: { ledger: 10, ha: 5 },
	status: { name: 'load-forecast', ok: true, message: 'ok', since: null, details: {} }
};

describe('legendItems', () => {
	it('nimmt die Einträge der Übersicht in Reihenfolge, mit Farbe und Marke', () => {
		const option = priceForecastOption(
			[price],
			forecast,
			load,
			'2026-10-09T08:05:00+00:00',
			palette,
			{
				measured: [[Date.UTC(2026, 9, 9, 8, 0), 50]],
				plan: { ...PLAN, valid: true }
			}
		);
		const items = legendItems(option);
		expect(items.map((item) => item.name)).toEqual([
			'Preis',
			'Einspeisung',
			'PV-Spanne',
			'PV',
			'Haus',
			'Akku',
			'Akku-Plan'
		]);
		expect(items.map((item) => item.mark)).toEqual([
			'line',
			'dashed',
			'area',
			'line',
			'dashed',
			'line',
			'dashed'
		]);
		expect(items[0].color).toBe(palette.importPrice);
		expect(items[2].color).toBe(palette.pv);
		expect(items[4].color).toBe(palette.load);
	});

	it('führt im Akku-Diagramm des Zeitplans Netzladen und Halten als Vierecke', () => {
		const items = legendItems(planSocOption(planWithWindows(), NOW, palette));
		expect(items.map((item) => item.name)).toEqual([
			'Geplanter SOC',
			'Reserve',
			'Netzladen',
			'Halten'
		]);
		expect(items.filter((item) => item.mark === 'rect').map((item) => item.name)).toEqual([
			'Netzladen',
			'Halten'
		]);
		expect(items[1].mark).toBe('dashed');
	});

	it('zeichnet Balken als Vierecke und nimmt ohne legend.data alle benannten Reihen', () => {
		const items = legendItems(statsEnergyOption(stats({ period: 'week' }), palette));
		expect(items.map((item) => [item.name, item.mark])).toEqual([
			['PV', 'rect'],
			['Bezug', 'rect'],
			['Einspeisung', 'rect'],
			['Verbrauch', 'line']
		]);
	});

	it('überspringt Reihen ohne Namen und liefert ohne legend nichts', () => {
		expect(legendItems({ series: [{ type: 'line', name: 'A' }] })).toEqual([]);
		const items = legendItems({
			legend: { show: false },
			series: [
				{ type: 'line', data: [] },
				{ type: 'line', name: 'B', itemStyle: { color: '#123' } }
			]
		});
		expect(items).toEqual([{ name: 'B', color: '#123', mark: 'line' }]);
	});
});

describe('withSelection', () => {
	it('setzt selected für alle Einträge, false für ausgeblendete', () => {
		const option = priceForecastOption(
			[price],
			forecast,
			load,
			'2026-10-09T08:05:00+00:00',
			palette
		);
		const selected = (
			withSelection(option, new Set(['PV'])) as {
				legend: { selected: Record<string, boolean> };
			}
		).legend.selected;
		expect(selected.PV).toBe(false);
		expect(selected.Preis).toBe(true);
		expect(Object.keys(selected)).toEqual(legendItems(option).map((item) => item.name));
	});

	it('lässt eine Option ohne legend unverändert', () => {
		const option = { series: [] };
		expect(withSelection(option, new Set(['PV']))).toBe(option);
	});
});
