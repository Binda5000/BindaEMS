import { expect, it } from 'vitest';
import { formatNumber } from '$lib/format';
import type { QualityDailyRow, QualityRow } from '$lib/api/schemas';
import {
	backtestRows,
	compareRows,
	featureLabel,
	groupDays,
	dailySeries,
	dataRange,
	featureRows,
	improvementText,
	modelStamp,
	pvRows,
	qualityRows,
	qualitySummary
} from './view';

function row(overrides: Partial<QualityRow>): QualityRow {
	return {
		kind: 'pv',
		horizon: 'h0_6',
		n: 96,
		mae_w: 100,
		bias_w: 10,
		rel_mae_pct: 12,
		improvement_pct: null,
		...overrides
	};
}

it('ordnet die Arten und Vorläufe wie im Text', () => {
	const rows = qualityRows([
		row({ kind: 'load_model', horizon: 'h24' }),
		row({ kind: 'load_profile', horizon: 'h0_6' }),
		row({ kind: 'load', horizon: 'h6_24' }),
		row({ kind: 'load_model', horizon: 'h0_6' }),
		row({ kind: 'pv', horizon: 'h24' }),
		row({ kind: 'pv', horizon: 'h0_6' }),
		row({ kind: 'load', horizon: 'h0_6' })
	]);
	expect(rows.map((r) => [r.kind, r.horizon])).toEqual([
		['PV', 'bis 6 h'],
		['PV', 'über 24 h'],
		['Last (verwendet)', 'bis 6 h'],
		['Last (verwendet)', '6 bis 24 h'],
		['Last Profil', 'bis 6 h'],
		['Last Modell', 'bis 6 h'],
		['Last Modell', 'über 24 h']
	]);
	expect(new Set(rows.map((r) => r.key)).size).toBe(rows.length);
});

it('formatiert die Kennzahlen einer Zeile', () => {
	const [view] = qualityRows([row({ n: 1234, mae_w: 412.5, bias_w: -80, rel_mae_pct: 18.34 })]);
	expect(view).toMatchObject({
		slots: formatNumber(1234, 0),
		mae: '413 W',
		bias: '-80 W',
		rel: '18,3 %',
		better: '–'
	});
	expect(qualityRows([row({ rel_mae_pct: null })])[0]?.rel).toBe('–');
});

it('zeigt die Verbesserung nur bei Last Modell', () => {
	const rows = qualityRows([
		row({ kind: 'load_profile', improvement_pct: 12 }),
		row({ kind: 'load_model', improvement_pct: 15.4 }),
		row({ kind: 'load_model', horizon: 'h6_24', improvement_pct: null })
	]);
	expect(rows.map((r) => r.better)).toEqual(['–', '+15 %', '–']);
});

it('schreibt die Verbesserung mit Vorzeichen', () => {
	expect(improvementText(15)).toBe('+15 %');
	expect(improvementText(14.6)).toBe('+15 %');
	expect(improvementText(-4)).toBe('-4 %');
	expect(improvementText(-0.2)).toBe('0 %');
	expect(improvementText(0)).toBe('0 %');
	expect(improvementText(null)).toBe('–');
});

it('trennt Profil und Modell je Tag, nach Datum', () => {
	const daily: QualityDailyRow[] = [
		{ date: '2026-10-02', kind: 'load_model', n: 96, mae_w: 90 },
		{ date: '2026-10-01', kind: 'load_model', n: 96, mae_w: 95 },
		{ date: '2026-10-01', kind: 'load_profile', n: 96, mae_w: 120 },
		{ date: '2026-10-01', kind: 'load', n: 96, mae_w: 120 },
		{ date: '2026-10-01', kind: 'pv', n: 96, mae_w: 300 },
		{ date: '2026-10-02', kind: 'load_profile', n: 96, mae_w: 118 }
	];
	expect(dailySeries(daily)).toEqual({
		profile: [
			{ date: '2026-10-01', mae_w: 120 },
			{ date: '2026-10-02', mae_w: 118 }
		],
		model: [
			{ date: '2026-10-01', mae_w: 95 },
			{ date: '2026-10-02', mae_w: 90 }
		]
	});
	expect(dailySeries([])).toEqual({ profile: [], model: [] });
});

it('ordnet die Merkmale absteigend und nennt den Anteil', () => {
	expect(
		featureRows([
			{ name: 'month', label: 'Monat', importance: 0 },
			{ name: 'temp_c', label: 'Temperatur', importance: 0.28 },
			{ name: 'weekday', label: 'Wochentag', importance: 0.015 }
		])
	).toEqual([
		{ name: 'temp_c', label: 'Temperatur', share: 28, text: '28,0 %' },
		{ name: 'weekday', label: 'Wochentag', share: 1.5, text: '1,5 %' },
		{ name: 'month', label: 'Monat', share: 0, text: '0,0 %' }
	]);
});

it('nennt Zeitpunkt und Datenbereich des Modells', () => {
	expect(modelStamp('2026-10-09T01:15:00+00:00')).toBe('09.10.2026 03:15');
	expect(dataRange('2026-08-14T01:15:00+00:00', '2026-10-09T01:15:00+00:00', 5376)).toBe(
		`14.08.–09.10.2026 · ${formatNumber(5376, 0)} Viertelstunden`
	);
});

it('ordnet den Backtest nach Woche und Vorlauf und zählt die Slots zusammen', () => {
	const week = (origin: string, horizon: 'h0_6' | 'h6_24' | 'h24', model: number) => ({
		origin,
		end: '2026-09-24T22:00:00+00:00',
		horizon,
		n: 840,
		model_mae_w: model,
		profile_mae_w: 200,
		improvement_pct: (1 - model / 200) * 100
	});
	const view = backtestRows({
		weeks: [
			week('2026-09-24T22:00:00+00:00', 'h0_6', 150),
			week('2026-09-17T22:00:00+00:00', 'h24', 210),
			week('2026-09-17T22:00:00+00:00', 'h0_6', 180)
		],
		total: { model_mae_w: 180, profile_mae_w: 200, improvement_pct: 10 }
	});
	expect(view.weeks.map((w) => [w.origin, w.horizon, w.better])).toEqual([
		['18.09.2026', 'bis 6 h', '+10\u00a0%'],
		['18.09.2026', 'über 24 h', '-5\u00a0%'],
		['25.09.2026', 'bis 6 h', '+25\u00a0%']
	]);
	expect(view.weeks[0]).toMatchObject({ slots: '840', model: '180\u00a0W', profile: '200\u00a0W' });
	expect(view.total).toEqual({
		slots: formatNumber(2520, 0),
		model: '180\u00a0W',
		profile: '200\u00a0W',
		better: '+10\u00a0%'
	});
});

const DAY_NAMES = ['Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa', 'So'];

function group(cells: string[][]) {
	return groupDays(
		cells.map((values, index) => ({ name: DAY_NAMES[index]!, values })),
		(day) => day.name,
		(day) => day.values
	);
}

it('groupDays fasst gleiche Tage hintereinander zu einer Zeile zusammen', () => {
	const werktag = ['07:15', '41 %'];
	const wochenende = ['–', '–'];
	expect(group([werktag, werktag, werktag, werktag, werktag, wochenende, wochenende])).toEqual([
		{ label: 'Mo bis Fr', cells: werktag },
		{ label: 'Sa, So', cells: wochenende }
	]);
});

it('groupDays lässt verschiedene Tage einzeln', () => {
	expect(group([['a'], ['b']])).toEqual([
		{ label: 'Mo', cells: ['a'] },
		{ label: 'Di', cells: ['b'] }
	]);
});

it('groupDays fasst nur aufeinanderfolgende Tage zusammen', () => {
	expect(group([['a'], ['b'], ['a']]).map((row) => row.label)).toEqual(['Mo', 'Di', 'Mi']);
});

it('groupDays gibt für keine Tage keine Zeilen', () => {
	expect(group([])).toEqual([]);
});

const demo = (kind: 'load_profile' | 'load_model', maes: [number, number, number]) =>
	(['h0_6', 'h6_24', 'h24'] as const).map((horizon, i) =>
		row({ kind, horizon, n: 96, mae_w: maes[i] ?? 0 })
	);

it('fasst die Güte in einem Satz zusammen (Demo-Zahlen: 29 % näher)', () => {
	const rows = [...demo('load_profile', [115, 142, 169]), ...demo('load_model', [82, 101, 120])];
	expect(qualitySummary(rows)).toBe(
		'Das Lastmodell lag im Mittel 29\u00a0% näher am echten Verbrauch als das Profil.'
	);
});

it('nennt im Satz auch das schlechtere Modell und den Gleichstand', () => {
	expect(
		qualitySummary([
			...demo('load_profile', [100, 100, 100]),
			...demo('load_model', [120, 120, 120])
		])
	).toBe('Das Profil lag im Mittel 20\u00a0% näher am echten Verbrauch als das Lastmodell.');
	expect(
		qualitySummary([
			...demo('load_profile', [100, 100, 100]),
			...demo('load_model', [100, 100, 100])
		])
	).toBe('Lastmodell und Profil lagen gleich nah am echten Verbrauch.');
});

it('gewichtet den Satz mit den Viertelstunden und zählt nur Vorläufe mit Profil und Modell', () => {
	const rows = [
		row({ kind: 'load_profile', horizon: 'h0_6', n: 100, mae_w: 100 }),
		row({ kind: 'load_model', horizon: 'h0_6', n: 100, mae_w: 50 }),
		// ohne Modellzeile: zählt nicht
		row({ kind: 'load_profile', horizon: 'h24', n: 900, mae_w: 500 })
	];
	expect(qualitySummary(rows)).toBe(
		'Das Lastmodell lag im Mittel 50\u00a0% näher am echten Verbrauch als das Profil.'
	);
});

it('hat ohne Modellzeilen keinen Satz', () => {
	expect(qualitySummary([])).toBeNull();
	expect(qualitySummary(demo('load_profile', [115, 142, 169]))).toBeNull();
	expect(qualitySummary([row({ kind: 'load_model' })])).toBeNull();
});

it('stellt Profil, Modell und verwendete Last je Vorlauf nebeneinander', () => {
	const rows = [
		row({ kind: 'load_model', horizon: 'h24', mae_w: 120 }),
		row({ kind: 'load_profile', horizon: 'h24', mae_w: 169 }),
		row({ kind: 'load', horizon: 'h0_6', mae_w: 113 }),
		row({ kind: 'load_profile', horizon: 'h0_6', mae_w: 115 }),
		row({ kind: 'load_model', horizon: 'h0_6', mae_w: 82 }),
		row({ kind: 'pv', horizon: 'h6_24', mae_w: 300 })
	];
	expect(compareRows(rows)).toEqual([
		{ horizon: 'bis 6 h', profile: '115\u00a0W', model: '82\u00a0W', used: '113\u00a0W' },
		{ horizon: 'über 24 h', profile: '169\u00a0W', model: '120\u00a0W', used: '–' }
	]);
	expect(compareRows([])).toEqual([]);
});

it('schreibt die PV-Zeilen als Satz', () => {
	expect(
		pvRows([
			row({ kind: 'pv', horizon: 'h24', n: 12, mae_w: 310 }),
			row({ kind: 'pv', horizon: 'h0_6', n: 4, mae_w: 240 }),
			row({ kind: 'load', horizon: 'h0_6' })
		])
	).toEqual([
		'PV-Prognose, bis 6 h: 240\u00a0W (4 Viertelstunden)',
		'PV-Prognose, über 24 h: 310\u00a0W (12 Viertelstunden)'
	]);
});

it('schreibt eine einzelne Viertelstunde in der Einzahl', () => {
	expect(pvRows([row({ kind: 'pv', horizon: 'h0_6', n: 1, mae_w: 240 })])).toEqual([
		'PV-Prognose, bis 6 h: 240\u00a0W (1 Viertelstunde)'
	]);
});

it('macht aus Sensor-Entitäten lesbare Merkmale', () => {
	expect(featureLabel('sensor.wohnzimmer_temperatur')).toBe('Wohnzimmer temperatur (Sensor)');
	expect(featureLabel('sensor.aussentemperatur')).toBe('Aussentemperatur (Sensor)');
	expect(featureLabel('Temperatur')).toBe('Temperatur');
});
