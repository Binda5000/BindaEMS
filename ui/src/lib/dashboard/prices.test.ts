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
	const next = [10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21].map((ct, i) =>
		slot(15 * (i + 1), ct)
	);
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

// Wie der Server (`/api/prices/now`): `now` ist der laufende Slot (10:00), `next_3h` beginnt danach (10:15)
function current(nowCt: number | null, next: number[]) {
	return priceSummary({
		now: nowCt === null ? null : slot(0, nowCt),
		next_3h: next.map((ct, i) => slot(15 * (i + 1), ct))
	});
}

describe('cheapestNext', () => {
	it('liefert den niedrigsten Preis der nächsten 3 h', () => {
		const summary = current(14, [13, 11.5, 12, 15]);
		expect(cheapestNext(summary)).toEqual({ start: summary.next[1]?.start, ct: 11.5, now: false });
	});

	it('die nächste Viertelstunde ist nicht „jetzt“', () => {
		const summary = current(14, [11, 12, 13]);
		expect(cheapestNext(summary)).toEqual({ start: summary.next[0]?.start, ct: 11, now: false });
	});

	it('nimmt bei Gleichstand den frühesten Slot', () => {
		const summary = current(14, [12, 11, 13, 11]);
		expect(cheapestNext(summary)?.start).toBe(summary.next[1]?.start);
	});

	it('meldet „jetzt“, wenn der jetzige Preis am günstigsten ist, auch bei Gleichstand', () => {
		expect(cheapestNext(current(10, [11, 12]))).toEqual({ start: null, ct: 10, now: true });
		expect(cheapestNext(current(11, [11, 12]))).toEqual({ start: null, ct: 11, now: true });
	});

	it('ohne jetzigen Preis zählen nur die nächsten Slots', () => {
		const summary = current(null, [11, 12]);
		expect(cheapestNext(summary)).toEqual({ start: summary.next[0]?.start, ct: 11, now: false });
	});

	it('ohne Preise der nächsten 3 h gibt es keinen günstigsten Slot', () => {
		expect(cheapestNext(current(10, []))).toBeNull();
		expect(cheapestNext(current(null, []))).toBeNull();
	});
});

describe('Texte der Preis-Kachel', () => {
	it('nennt den günstigsten Slot oder „jetzt“', () => {
		expect(cheapestText(current(30, [20, 25]))).toBe('am günstigsten um 10:15');
		expect(cheapestText(current(30, [25, 20]))).toBe('am günstigsten um 10:30');
		expect(cheapestText(current(10, [20, 25]))).toBe('jetzt am günstigsten');
		expect(cheapestText(current(10, []))).toBeNull();
	});

	it('fasst die Spanne der nächsten 3 h zusammen', () => {
		expect(priceRangeText(current(30, [30, 20, 25]))).toMatch(
			/^Preise der nächsten 3 Stunden: 20,00\sct\/kWh bis 30,00\sct\/kWh$/
		);
		expect(priceRangeText(current(30, []))).toBe('Preise der nächsten 3 Stunden');
	});
});
