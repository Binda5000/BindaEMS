// Strompreis jetzt und die Stufen der nächsten 3 h (Drittel der Spanne); Prognosen fürs Diagramm
import type { PricesNow } from '$lib/api/schemas';
import { formatCt, formatTime } from '$lib/format';
import { addDays, localRange, parseIso, todayVienna } from '$lib/time';

export type PriceLevel = 'low' | 'mid' | 'high';

export interface PriceSummary {
	nowCt: number | null;
	feedInCt: number | null;
	origin: 'primary' | 'fallback' | null;
	/** fehlende Tarifbestandteile, als 0 gerechnet */
	incomplete: boolean;
	next: { start: string; ct: number; level: PriceLevel }[];
}

export function priceSummary(data: PricesNow): PriceSummary {
	const prices = data.next_3h.map((slot) => slot.import_gross_ct);
	const min = Math.min(...prices);
	const span = Math.max(...prices) - min;
	const level = (ct: number): PriceLevel => {
		if (!(span > 0)) return 'mid';
		const position = (ct - min) / span;
		return position < 1 / 3 ? 'low' : position < 2 / 3 ? 'mid' : 'high';
	};
	return {
		nowCt: data.now?.import_gross_ct ?? null,
		feedInCt: data.now?.feed_in_ct ?? null,
		origin: data.now?.origin ?? null,
		incomplete: (data.now?.missing.length ?? 0) > 0,
		next: data.next_3h.map((slot) => ({
			start: slot.start,
			ct: slot.import_gross_ct,
			level: level(slot.import_gross_ct)
		}))
	};
}

/**
 * PV- und Lastprognose für das Diagramm „heute/morgen“: heute und morgen in Wien, unabhängig davon, ob die
 * Preise für morgen schon da sind (die kommen erst am Nachmittag; Open-Meteo reicht drei Tage).
 */
export function forecastForChart<T extends { start: string }>(forecast: T[], now: Date): T[] {
	const today = todayVienna(now);
	const range = localRange(today, addDays(today, 1));
	const from = parseIso(range.from) ?? Number.NaN;
	const to = parseIso(range.to) ?? Number.NaN;
	return forecast.filter((slot) => {
		const start = parseIso(slot.start);
		return start !== null && start >= from && start < to;
	});
}

/** Günstigster Slot der nächsten 3 h (bei Gleichstand der früheste); `now`, wenn es der erste ist */
export function cheapestNext(
	summary: PriceSummary
): { start: string; ct: number; now: boolean } | null {
	let best = -1;
	summary.next.forEach((slot, index) => {
		if (best < 0 || slot.ct < (summary.next[best]?.ct ?? Infinity)) best = index;
	});
	const slot = summary.next[best];
	return slot ? { start: slot.start, ct: slot.ct, now: best === 0 } : null;
}

/** „am günstigsten um 10:15“ bzw. „jetzt am günstigsten“; ohne Slots `null` */
export function cheapestText(summary: PriceSummary): string | null {
	const cheapest = cheapestNext(summary);
	if (!cheapest) return null;
	return cheapest.now ? 'jetzt am günstigsten' : `am günstigsten um ${formatTime(cheapest.start)}`;
}

/** Beschriftung der Mini-Balken: die Spanne der nächsten 3 h */
export function priceRangeText(summary: PriceSummary): string {
	const prices = summary.next.map((slot) => slot.ct);
	if (prices.length === 0) return 'Preise der nächsten 3 Stunden';
	return `Preise der nächsten 3 Stunden: ${formatCt(Math.min(...prices))} bis ${formatCt(Math.max(...prices))}`;
}
