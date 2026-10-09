// Strompreis jetzt und die Stufen der nächsten 3 h (Drittel der Spanne)
import type { PricesNow } from '$lib/api/schemas';

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
