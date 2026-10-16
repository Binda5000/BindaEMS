import { expect, it } from 'vitest';
import { INTENT_LABELS, SOURCE_LABELS, TRIGGER_LABELS, blockLabel, planBlocks } from './model';
import { iso, slot } from '$lib/testing/plan';

it('groups slots into blocks by intent, power and main code', () => {
	const blocks = planBlocks([
		slot('10:00', 'HALTEN', 0, 'HALTEN_FUER_SPITZE'),
		slot('10:15', 'HALTEN', 0, 'HALTEN_FUER_SPITZE'),
		slot('10:30', 'NETZLADEN', 2300, 'NETZLADEN_GUENSTIG'),
		slot('10:45', 'NETZLADEN', 2000, 'NETZLADEN_GUENSTIG')
	]);
	expect(blocks.map((b) => [b.start, b.end, blockLabel(b)])).toEqual([
		[iso('10:00'), iso('10:30'), 'Halten'],
		[iso('10:30'), iso('10:45'), 'Netzladen 2,3 kW'],
		[iso('10:45'), iso('11:00'), 'Netzladen 2 kW']
	]);
});

it('splits blocks with the same intent but a different main code', () => {
	const blocks = planBlocks([
		slot('10:00', 'EIGENVERBRAUCH', 0, 'PV_LADEN'),
		slot('10:15', 'EIGENVERBRAUCH', 0, 'PV_LADEN'),
		slot('10:30', 'EIGENVERBRAUCH', 0, 'EIGENVERBRAUCH')
	]);
	expect(blocks.map((b) => [b.start, b.end, blockLabel(b)])).toEqual([
		[iso('10:00'), iso('10:30'), 'Eigenverbrauch'],
		[iso('10:30'), iso('10:45'), 'Eigenverbrauch']
	]);
});

it('ignores the estimated-price and fallback codes when picking the main code', () => {
	const blocks = planBlocks([
		slot('10:00', 'EIGENVERBRAUCH', 0, 'ERSATZPLAN', { codes: ['ERSATZPLAN', 'EIGENVERBRAUCH'] }),
		slot('10:15', 'EIGENVERBRAUCH', 0, 'ERSATZPLAN', {
			codes: ['ERSATZPLAN', 'EIGENVERBRAUCH', 'GESCHAETZT']
		})
	]);
	expect(blocks).toHaveLength(1);
});

it('carries SOC from the end of the slot before the block', () => {
	const blocks = planBlocks([
		slot('10:00', 'HALTEN', 0, 'HALTEN_RESTWERT', { soc_pct: 40 }),
		slot('10:15', 'HALTEN', 0, 'HALTEN_RESTWERT', { soc_pct: 41 }),
		slot('10:30', 'NETZLADEN', 2300, 'NETZLADEN_GUENSTIG', { soc_pct: 47.4 }),
		slot('10:45', 'NETZLADEN', 2300, 'NETZLADEN_GUENSTIG', { soc_pct: 53.6 })
	]);
	expect(blocks.map((b) => [b.socFrom, b.socTo])).toEqual([
		[40, 41],
		[41, 53.6]
	]);
	expect(blocks[1].text).toBe('Satz NETZLADEN_GUENSTIG');
});

it('returns no blocks without slots', () => {
	expect(planBlocks([])).toEqual([]);
});

it('names intents, triggers and sources in German', () => {
	expect(INTENT_LABELS).toEqual({
		EIGENVERBRAUCH: 'Eigenverbrauch',
		HALTEN: 'Halten',
		NETZLADEN: 'Netzladen'
	});
	expect(TRIGGER_LABELS).toEqual({
		start: 'Start',
		slot: 'Viertelstunde',
		prices: 'neue Preise',
		pv_forecast: 'neue PV-Prognose',
		load_forecast: 'neue Lastprognose',
		soc: 'SOC weicht ab',
		month_peak: 'neue Monatsspitze',
		settings: 'Einstellungen geändert',
		manual: 'Neu planen'
	});
	expect(SOURCE_LABELS).toEqual({ optimizer: 'Optimierer', fallback: 'Ersatzplan' });
});
