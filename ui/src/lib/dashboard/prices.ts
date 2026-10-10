// Strompreis jetzt und die Stufen der nächsten 3 h (Drittel der Spanne); PV-Prognose fürs Diagramm
import type { ForecastSlot, PricesNow } from '$lib/api/schemas';
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
 * PV-Prognose für das Diagramm „heute/morgen“: heute und morgen in Wien, unabhängig davon, ob die
 * Preise für morgen schon da sind (die kommen erst am Nachmittag; Open-Meteo reicht drei Tage).
 */
export function forecastForChart(forecast: ForecastSlot[], now: Date): ForecastSlot[] {
	const today = todayVienna(now);
	const range = localRange(today, addDays(today, 1));
	const from = parseIso(range.from) ?? Number.NaN;
	const to = parseIso(range.to) ?? Number.NaN;
	return forecast.filter((slot) => {
		const start = parseIso(slot.start);
		return start !== null && start >= from && start < to;
	});
}
