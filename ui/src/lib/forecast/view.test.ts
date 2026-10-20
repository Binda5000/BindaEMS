import { expect, it } from 'vitest';
import { formatNumber } from '$lib/format';
import type { QualityDailyRow, QualityRow } from '$lib/api/schemas';
import {
	backtestRows,
	dailySeries,
	dataRange,
	featureRows,
	improvementText,
	modelStamp,
	qualityRows
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
		['PV', '0–6 h'],
		['PV', 'über 24 h'],
		['Last (verwendet)', '0–6 h'],
		['Last (verwendet)', '6–24 h'],
		['Last Profil', '0–6 h'],
		['Last Modell', '0–6 h'],
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
	expect(improvementText(-4)).toBe('−4 %');
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
		['18.09.2026', '0–6 h', '+10\u00a0%'],
		['18.09.2026', 'über 24 h', '\u22125\u00a0%'],
		['25.09.2026', '0–6 h', '+25\u00a0%']
	]);
	expect(view.weeks[0]).toMatchObject({ slots: '840', model: '180\u00a0W', profile: '200\u00a0W' });
	expect(view.total).toEqual({
		slots: formatNumber(2520, 0),
		model: '180\u00a0W',
		profile: '200\u00a0W',
		better: '+10\u00a0%'
	});
});
