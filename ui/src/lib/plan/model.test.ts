import { expect, it } from 'vitest';
import {
	INTENT_LABELS,
	SOURCE_LABELS,
	TRIGGER_LABELS,
	RESERVE_RECHARGE_LABEL,
	blockLabel,
	intentLabel,
	STORM_KIND_LABELS,
	STORM_LEVEL_LABELS,
	planBlocks,
	socRange,
	stormWarningLines
} from './model';
import type { PlanStormWarning, PlanVehicle } from '$lib/api/schemas';
import { NOW, PLAN, iso, slot } from '$lib/testing/plan';

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

it('nennt ein Nachladen auf die Reserve „Nachladen“, mit dem Satz des Servers', () => {
	const text = 'Nachladen auf die Reserve, bevor der Victron selbst mit voller Leistung nachlädt.';
	const recharge = { reserve_recharge: true, text };
	const blocks = planBlocks([
		slot('10:00', 'NETZLADEN', 3000, 'NACHLADEN_RESERVE', recharge),
		slot('10:15', 'NETZLADEN', 3000, 'NACHLADEN_RESERVE', recharge),
		slot('10:30', 'NETZLADEN', 3000, 'NETZLADEN_GUENSTIG')
	]);
	expect(blocks.map((b) => [b.start, b.end, blockLabel(b), b.text])).toEqual([
		[iso('10:00'), iso('10:30'), 'Nachladen 3 kW', text],
		[iso('10:30'), iso('10:45'), 'Netzladen 3 kW', 'Satz NETZLADEN_GUENSTIG']
	]);
	expect(RESERVE_RECHARGE_LABEL).toBe('Nachladen');
	expect(intentLabel('NETZLADEN', 1500, true)).toBe('Nachladen 1,5 kW');
	expect(intentLabel('NETZLADEN', 1500, false)).toBe('Netzladen 1,5 kW');
	expect(intentLabel('NETZLADEN', 1500)).toBe('Netzladen 1,5 kW');
});

it('zeigt im Fenster der Unwetter-Reserve den Satz des Servers als eigenen Block', () => {
	const text = 'Unwetter-Reserve: Gewitter (orange) bis 16:00 Uhr, der Akku hält mindestens 40 %.';
	const storm = { storm_reserve_pct: 40, text };
	const blocks = planBlocks([
		slot('13:00', 'HALTEN', 0, 'HALTEN_FUER_SPITZE'),
		slot('13:15', 'HALTEN', 0, 'UNWETTER_RESERVE', storm),
		slot('13:30', 'HALTEN', 0, 'UNWETTER_RESERVE', storm),
		slot('16:00', 'EIGENVERBRAUCH', 0, 'EIGENVERBRAUCH')
	]);
	expect(blocks.map((b) => [b.start, blockLabel(b), b.text])).toEqual([
		[iso('13:00'), 'Halten', 'Satz HALTEN_FUER_SPITZE'],
		[iso('13:15'), 'Halten', text],
		[iso('16:00'), 'Eigenverbrauch', 'Satz EIGENVERBRAUCH']
	]);
});

const warning = (changes: Partial<PlanStormWarning> = {}): PlanStormWarning => ({
	warn_id: 7,
	version: 1,
	kind: 'gewitter',
	level: 2,
	begin: iso('14:00'),
	end: iso('16:00'),
	text: 'Gewitter mit Sturmböen',
	...changes
});

it('nennt Arten und Stufen der Unwetterwarnungen auf Deutsch', () => {
	expect(STORM_KIND_LABELS).toEqual({
		sturm: 'Sturm',
		regen: 'Regen',
		schnee: 'Schnee',
		glatteis: 'Glatteis',
		gewitter: 'Gewitter',
		hitze: 'Hitze',
		kaelte: 'Kälte'
	});
	expect(STORM_LEVEL_LABELS).toEqual({ 1: 'gelb', 2: 'orange', 3: 'rot' });
});

it('zeigt jede Unwetterwarnung des Plans mit Beginn und Ende, an einem anderen Tag mit Tag', () => {
	const now = new Date(NOW); // 09.10. 10:00 in Wien
	expect(stormWarningLines(null, now)).toEqual([]);
	expect(stormWarningLines({ target_pct: 40, warnings: [] }, now)).toEqual([]);
	const warnings = [
		warning(),
		// morgen, über Mitternacht und an einem späteren Tag (auch hinter dem Horizont, Ruling R12)
		warning({
			warn_id: 8,
			kind: 'sturm',
			level: 3,
			begin: '2026-10-10T04:00:00+00:00',
			end: '2026-10-10T08:30:00+00:00'
		}),
		warning({
			warn_id: 9,
			kind: 'kaelte',
			level: 1,
			begin: '2026-10-09T20:00:00+00:00',
			end: '2026-10-10T06:00:00+00:00'
		}),
		warning({
			warn_id: 10,
			kind: 'glatteis',
			begin: '2026-10-12T06:00:00+00:00',
			end: '2026-10-12T10:00:00+00:00'
		})
	];
	expect(stormWarningLines({ target_pct: 40, warnings }, now)).toEqual([
		'Unwetterwarnung: Gewitter (orange) 14:00–16:00',
		'Unwetterwarnung: Sturm (rot) morgen 06:00–10:30',
		'Unwetterwarnung: Kälte (gelb) 22:00–morgen 08:00',
		'Unwetterwarnung: Glatteis (orange) Mo 12.10. 08:00–12:00'
	]);
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
		manual: 'Neu planen',
		vehicles: 'Fahrzeug geändert',
		thermal: 'Klimaanlage geändert',
		storm: 'Unwetterwarnung'
	});
	expect(SOURCE_LABELS).toEqual({ optimizer: 'Optimierer', fallback: 'Ersatzplan' });
});

const expectedCar = (changes: Partial<PlanVehicle> = {}): PlanVehicle => ({
	...PLAN.vehicles[0]!,
	expected: true,
	arrival_at: iso('10:20'),
	...changes
});

it('hängt ein erwartetes Auto an den Block seiner Ankunft', () => {
	const slots = [
		slot('10:00', 'EIGENVERBRAUCH', 0, 'EIGENVERBRAUCH'),
		slot('10:15', 'NETZLADEN', 2300, 'NETZLADEN_GUENSTIG'),
		slot('10:30', 'NETZLADEN', 2000, 'NETZLADEN_GUENSTIG')
	];
	const blocks = planBlocks(slots, [expectedCar()]);
	expect(blocks.map((block) => block.expected)).toEqual([
		[],
		['Tesla Model 3 erwartet ab 10:20'],
		[]
	]);
});

it('ignoriert angesteckte Autos, erwartete ohne Ankunft und Ankünfte außerhalb des Plans', () => {
	const slots = [slot('10:00', 'EIGENVERBRAUCH', 0, 'EIGENVERBRAUCH')];
	const blocks = planBlocks(slots, [
		expectedCar({ expected: false }),
		expectedCar({ arrival_at: null }),
		expectedCar({ arrival_at: iso('14:00') })
	]);
	expect(blocks[0]?.expected).toEqual([]);
});

it('legt eine Ankunft vor dem ersten Slot in den ersten Block', () => {
	const slots = [slot('10:00', 'EIGENVERBRAUCH', 0, 'EIGENVERBRAUCH')];
	const blocks = planBlocks(slots, [expectedCar({ arrival_at: iso('09:00') })]);
	expect(blocks[0]?.expected).toEqual(['Tesla Model 3 erwartet ab 09:00']);
});

it('startet den ersten Block beim Start-SOC des Plans statt am Ende des ersten Slots', () => {
	const slots = [
		slot('10:00', 'HALTEN', 0, 'HALTEN_RESTWERT', { soc_pct: 20 }),
		slot('10:15', 'NETZLADEN', 2300, 'NETZLADEN_GUENSTIG', { soc_pct: 25 })
	];
	expect(planBlocks(slots, [], 17.4).map((b) => [b.socFrom, b.socTo])).toEqual([
		[17.4, 20],
		[20, 25]
	]);
	// ohne Start-SOC wie bisher
	expect(planBlocks(slots).map((b) => b.socFrom)).toEqual([20, 20]);
	expect(planBlocks(slots, [], null).map((b) => b.socFrom)).toEqual([20, 20]);
});

it('socRange: ganzzahlig, bei gleicher Rundung mit einer Nachkommastelle', () => {
	expect(socRange(19.2, 19.9)).toBe('19,2 → 19,9');
	expect(socRange(19, 20.4)).toBe('19 → 20');
	expect(socRange(17, 17)).toBe('17 → 17');
	expect(socRange(17.01, 17.03)).toBe('17 → 17');
});
