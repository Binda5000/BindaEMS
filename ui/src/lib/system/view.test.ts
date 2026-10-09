import { expect, it } from 'vitest';
import type { PriceStatus } from '$lib/api/schemas';
import { coreState, reading } from '$lib/testing/fixtures';
import { selfcheckLabel, signalRows, vatText } from './view';

it('benennt Selbstprüfungen deutsch', () => {
	expect(['dess', 'evcs_mode.twc', 'neu'].map(selfcheckLabel)).toEqual([
		'Dynamic ESS',
		'EVCS-Modus twc',
		'neu'
	]);
});

it('listet Signale mit Wert, Qualität und Alter', () => {
	const state = {
		...coreState(),
		signals: {
			'grid.power_w': reading(512.25),
			'vebus.mode': reading('ON', 'stale'),
			'vehicle.tesla.plugged': { v: true, ts: '2026-10-09T07:59:30+00:00', q: 'ok' as const },
			'battery.soc_pct': reading(null, 'invalid')
		}
	};
	const now = Date.UTC(2026, 9, 9, 8, 0, 10);
	expect(signalRows(state, now, '')).toEqual([
		{ name: 'battery.soc_pct', value: '–', quality: 'invalid', ageS: 10 },
		{ name: 'grid.power_w', value: '512,25', quality: 'ok', ageS: 10 },
		{ name: 'vebus.mode', value: 'ON', quality: 'stale', ageS: 10 },
		{ name: 'vehicle.tesla.plugged', value: 'ja', quality: 'ok', ageS: 40 }
	]);
	expect(signalRows(state, now, 'GRID').map((row) => row.name)).toEqual(['grid.power_w']);
});

it('beschreibt die Brutto/Netto-Erkennung', () => {
	const status = (part: Partial<PriceStatus>): PriceStatus => ({
		last_attempt: null,
		last_success: null,
		vat_mode: null,
		vat_detection: null,
		days: [],
		errors: [],
		...part
	});
	expect(
		vatText(
			status({ vat_mode: 'gross', vat_detection: { result: 'gross', ratio: 1.2, slots: 96 } })
		)
	).toBe('brutto (erkannt, Verhältnis 1,200 aus 96 Slots)');
	expect(
		vatText(status({ vat_mode: 'gross', vat_detection: { result: null, ratio: 1.1, slots: 40 } }))
	).toBe('brutto (Verhältnis 1,100 aus 40 Slots, nicht eindeutig)');
	expect(vatText(status({}))).toBe('noch nicht bestimmt');
});
