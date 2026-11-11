import * as v from 'valibot';
import { describe, expect, it } from 'vitest';
import pvJson from '$lib/api/contract/pv.json';
import pvMonthJson from '$lib/api/contract/pv-period-month.json';
import { PvPeriodSchema, PvResponseSchema } from '$lib/api/schemas';
import { HUAWEI, SUED, WEST, pvTree, pvWeek, pvYear } from '$lib/testing/pv';
import {
	cards,
	formatKwh,
	formatKwp,
	formatPerKwp,
	headline,
	periodLabel,
	sourceLabel
} from './tree';

describe('cards', () => {
	it('liefert je Anlage ihre Strings in sort-Reihenfolge und barPct je String', () => {
		// West zuerst in der Antwort, aber mit der höheren Reihenfolge
		const tree = pvTree([HUAWEI, { ...WEST, sort: 1 }, { ...SUED, sort: 0 }]);
		tree.tree[0]!.children.reverse();
		const [card, ...rest] = cards(tree, pvWeek());
		expect(rest).toEqual([]);
		expect(card?.name).toBe('Huawei');
		expect(card?.strings.map((row) => [row.name, row.barPct])).toEqual([
			['Süd', 100],
			['West', 86]
		]);
		expect(card?.kwh).toBe(49.6);
		expect(card?.perKwp).toBeCloseTo(5.061, 3);
		expect(card?.powerW).toBe(2960);
		expect(card?.stringsKwh).toBeCloseTo(51.1, 6);
	});

	it('führt die Antworten der API über die ID zusammen', () => {
		const tree = v.parse(PvResponseSchema, pvJson);
		const period = v.parse(PvPeriodSchema, pvMonthJson);
		const [card, ...rest] = cards(tree, period);
		expect(rest).toEqual([]);
		expect([card?.name, card?.source, card?.kwh, card?.powerW]).toEqual([
			'Huawei',
			'Victron-Zähler (AC)',
			period.sources[0]?.sum_kwh,
			tree.total_w
		]);
		expect(card?.strings.map((row) => row.name)).toEqual(['Süd', 'West']);
		expect(card?.strings[0]?.barPct).toBe(100);
	});

	it('sortiert Anlagen nach sort, dann Name', () => {
		const tree = pvTree([
			{ ...HUAWEI, id: 1, name: 'Zaun', sort: 0 },
			{ ...HUAWEI, id: 4, name: 'Balkon', sort: 0 },
			{ ...HUAWEI, id: 5, name: 'Alt', sort: 2 }
		]);
		expect(cards(tree).map((card) => card.name)).toEqual(['Balkon', 'Zaun', 'Alt']);
	});

	it('hat ohne Zeitraum keine Energie, aber die Leistung', () => {
		const [card] = cards(pvTree());
		expect(card?.kwh).toBeNull();
		expect(card?.powerW).toBe(2960);
		expect(card?.strings.map((row) => row.barPct)).toEqual([null, null]);
	});

	it('gibt Strings ohne kWp keinen Balken und keinen Wert je kWp', () => {
		const tree = pvTree([HUAWEI, SUED, { ...WEST, kwp: null }]);
		const period = pvWeek();
		period.sources[2]!.kwh_per_kwp = null;
		const [card] = cards(tree, period);
		expect(card?.strings.map((row) => [row.name, row.perKwp, row.barPct])).toEqual([
			['Süd', 5.683, 100],
			['West', null, null]
		]);
	});

	it('zeigt bei 0 kWh in allen Strings leere Balken statt einer Division durch 0', () => {
		const period = pvWeek();
		for (const source of period.sources) {
			source.sum_kwh = 0;
			source.kwh_per_kwp = 0;
		}
		const [card] = cards(pvTree(), period);
		expect(card?.strings.map((row) => row.barPct)).toEqual([0, 0]);
	});

	it('nennt den ersten Tag mit Wert, wenn frühe Tage fehlen', () => {
		const [card] = cards(pvTree(), pvYear());
		expect(card?.since).toBe('ab 30.08.');
		expect(card?.strings.map((row) => row.since)).toEqual(['ab 21.09.', 'ab 21.09.']);
		const [week] = cards(pvTree(), pvWeek());
		expect(week?.since).toBeNull();
	});

	it('nennt den Grund, wenn Leistung oder Energie fehlen', () => {
		const tree = pvTree();
		tree.tree[0] = { ...tree.tree[0]!, power_w: null, note: 'kein Wert in den letzten 24 h' };
		const period = pvWeek();
		period.sources[0] = {
			...period.sources[0]!,
			kwh: period.sources[0]!.kwh.map(() => null),
			sum_kwh: null,
			kwh_per_kwp: null,
			note: 'Verlauf nicht lesbar'
		};
		const [card] = cards(tree, period);
		expect(card?.powerNote).toBe('kein Wert in den letzten 24 h');
		expect(card?.energyNote).toBe('Verlauf nicht lesbar');
	});
});

describe('headline', () => {
	it('nennt PV gesamt im Zeitraum und jetzt, „nicht zugeordnet“ erst ab 0,1 kWh oder 50 W', () => {
		expect(headline(pvTree(), pvWeek(), true)).toEqual({
			kwh: 49.6,
			powerW: 2960,
			unassignedKwh: null
		});
		const period = pvWeek();
		period.unassigned = { kwh: [], sum_kwh: 1.2 };
		expect(headline(pvTree(), period, true).unassignedKwh).toBe(1.2);
		const tree = pvTree();
		tree.tree[1] = { ...tree.tree[1]!, power_w: 60 };
		expect(headline(tree, pvWeek(), true).unassignedKwh).toBe(0);
	});

	it('nimmt die Leistung jetzt nur für den laufenden Zeitraum', () => {
		const tree = pvTree();
		tree.tree[1] = { ...tree.tree[1]!, power_w: 60 };
		// voriges Jahr: dass jetzt 60 W nicht zugeordnet sind, sagt über damals nichts
		expect(headline(tree, pvWeek(), false).unassignedKwh).toBeNull();
		const period = pvWeek();
		period.unassigned = { kwh: [], sum_kwh: 1.2 };
		expect(headline(tree, period, false).unassignedKwh).toBe(1.2);
	});

	it('kennt ohne Anlagen kein „nicht zugeordnet“', () => {
		const period = pvWeek();
		period.unassigned = null;
		expect(headline(pvTree([]), period, true).unassignedKwh).toBeNull();
	});
});

describe('Formate', () => {
	it('formatiert kWp mit zwei, kWh mit einer Nachkommastelle', () => {
		expect(formatKwp(2)).toBe('2,00');
		expect(formatKwp(9.8)).toBe('9,80');
		expect(formatKwh(23.3)).toBe('23,3 kWh');
		expect(formatKwh(0)).toBe('0,0 kWh');
		expect(formatKwh(null)).toBe('–');
		expect(formatPerKwp(5.683)).toBe('5,68');
		expect(formatPerKwp(0)).toBe('0,00');
		expect(formatPerKwp(null)).toBe('–');
	});

	it('nennt die Quelle einer Anlage', () => {
		expect(sourceLabel(HUAWEI)).toBe('Victron-Zähler (AC)');
		expect(sourceLabel(SUED)).toBe('Home Assistant');
		expect(sourceLabel({ ...HUAWEI, power_ref: 'pv_dc.power_w' })).toBe('Victron-MPPT (DC)');
	});

	it('benennt den Zeitraum, den laufenden mit „Heute“ bzw. „Diese Woche“', () => {
		expect(periodLabel('day', true)).toBe('Heute');
		expect(periodLabel('day', false)).toBe('Am Tag');
		expect(periodLabel('week', true)).toBe('Diese Woche');
		expect(periodLabel('month', false)).toBe('Im Monat');
		expect(periodLabel('year', true)).toBe('Dieses Jahr');
	});
});
