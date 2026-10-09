import { expect, it } from 'vitest';
import type { PriceSlot } from '$lib/api/schemas';
import { priceSummary } from './prices';

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
