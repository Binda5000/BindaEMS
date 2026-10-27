import { describe, expect, it } from 'vitest';
import type { PriceSlot } from '$lib/api/schemas';
import {
	cheapestNext,
	cheapestText,
	forecastForChart,
	priceRangeText,
	priceSummary
} from './prices';

const slot = (minute: number, ct: number, extra: Partial<PriceSlot> = {}): PriceSlot => ({
	start: new Date(Date.UTC(2026, 9, 9, 8, minute)).toISOString(),
	spot_net_ct: 9,
	import_net_ct: ct / 1.2,
	import_gross_ct: ct,
	feed_in_ct: 7.3,
	origin: 'primary',
	missing: [],
	...extra
});

it('teilt die nächsten 3 h in günstig, mittel und teuer', () => {
	const next = [10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21].map((ct, i) => slot(15 * i, ct));
	const summary = priceSummary({ now: slot(0, 13.44), next_3h: next });
	expect([summary.nowCt, summary.feedInCt]).toEqual([13.44, 7.3]);
	expect(summary.next.map((n) => n.level)).toEqual([
		'low',
		'low',
		'low',
		'low',
		'mid',
		'mid',
		'mid',
		'mid',
		'high',
		'high',
		'high',
		'high'
	]);
});

it('meldet Ersatzquelle und fehlende Tarifwerte', () => {
	const now = slot(0, 13.44, { origin: 'fallback', missing: ['grid_loss'] });
	expect(priceSummary({ now, next_3h: [] })).toMatchObject({
		origin: 'fallback',
		incomplete: true
	});
});

it('ohne Preis für jetzt bleibt alles leer', () => {
	expect(priceSummary({ now: null, next_3h: [] })).toEqual({
		nowCt: null,
		feedInCt: null,
		origin: null,
		incomplete: false,
		next: []
	});
});

/** Viertelstunden ab `from` (UTC) als ISO-Zeitpunkte */
function quarterHours(from: string, count: number): string[] {
	return Array.from({ length: count }, (_, index) =>
		new Date(Date.parse(from) + index * 900_000).toISOString()
	);
}

it('zeigt die PV-Prognose für heute und morgen, auch bevor die Preise für morgen da sind', () => {
	// 09.10., 10:00 in Wien: Preise gibt es erst für heute, Open-Meteo liefert drei Tage
	const now = new Date('2026-10-09T08:00:00Z');
	const forecast = quarterHours('2026-10-08T22:00:00Z', 3 * 96).map((start) => ({
		start,
		p50_w: 1000,
		p25_w: 700,
		p75_w: 1200
	}));
	const shown = forecastForChart(forecast, now);
	expect(shown[0]?.start).toBe('2026-10-08T22:00:00.000Z'); // heute 00:00 in Wien
	expect(shown.at(-1)?.start).toBe('2026-10-10T21:45:00.000Z'); // morgen 23:45 in Wien
	expect(shown).toHaveLength(2 * 96);
});

describe('cheapestNext', () => {
	const summaryOf = (cts: number[]) =>
		priceSummary({ now: slot(0, cts[0] ?? 0), next_3h: cts.map((ct, i) => slot(15 * i, ct)) });

	it('liefert den niedrigsten Preis der nächsten 3 h', () => {
		const summary = summaryOf([14, 13, 11.5, 12, 15]);
		expect(cheapestNext(summary)).toEqual({ start: summary.next[2]?.start, ct: 11.5, now: false });
	});

	it('nimmt bei Gleichstand den frühesten Slot', () => {
		const summary = summaryOf([14, 11, 12, 11]);
		expect(cheapestNext(summary)?.start).toBe(summary.next[1]?.start);
	});

	it('meldet „jetzt“, wenn der erste Slot am günstigsten ist', () => {
		const summary = summaryOf([10, 11, 12]);
		expect(cheapestNext(summary)).toEqual({ start: summary.next[0]?.start, ct: 10, now: true });
	});

	it('ohne Preise gibt es keinen günstigsten Slot', () => {
		expect(cheapestNext(summaryOf([]))).toBeNull();
	});
});

describe('Texte der Preis-Kachel', () => {
	const data = (ct: number[]) =>
		priceSummary({ now: slot(0, ct[0] ?? 0), next_3h: ct.map((c, i) => slot(i * 15, c)) });

	it('nennt den günstigsten Slot oder „jetzt“', () => {
		expect(cheapestText(data([30, 20, 25]))).toBe('am günstigsten um 10:15');
		expect(cheapestText(data([10, 20, 25]))).toBe('jetzt am günstigsten');
		expect(cheapestText(data([]))).toBeNull();
	});

	it('fasst die Spanne der nächsten 3 h zusammen', () => {
		expect(priceRangeText(data([30, 20, 25]))).toMatch(
			/^Preise der nächsten 3 Stunden: 20,00\sct\/kWh bis 30,00\sct\/kWh$/
		);
		expect(priceRangeText(data([]))).toBe('Preise der nächsten 3 Stunden');
	});
});
