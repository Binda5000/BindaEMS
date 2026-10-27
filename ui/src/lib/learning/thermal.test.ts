import * as v from 'valibot';
import { describe, expect, it } from 'vitest';
import thermalJson from '$lib/api/contract/learning-thermal.json';
import {
	ThermalStatusSchema,
	type PlanSlot,
	type PlanThermal,
	type ThermalModeStatus
} from '$lib/api/schemas';
import { slot } from '$lib/testing/plan';
import {
	computedText,
	planThermalLine,
	plannedOffsetK,
	plannedUntil,
	thermalModeView,
	thermalRuns
} from './thermal';

const NB = ' ';
const MINUS = '−';

const STATUS = v.parse(ThermalStatusSchema, thermalJson);
const HEATING = STATUS.modes.find((mode) => mode.mode === 'heizen')!;
const COOLING = STATUS.modes.find((mode) => mode.mode === 'kuehlen')!;

function rows(status: ThermalModeStatus): Record<string, string> {
	return Object.fromEntries(thermalModeView(status).rows.map((row) => [row.label, row.value]));
}

describe('plannedOffsetK', () => {
	it('heizt mit +0,5 K je Stufe und kühlt mit −0,5 K je Stufe', () => {
		expect(plannedOffsetK(2, 'heizen')).toBe(1);
		expect(plannedOffsetK(-1, 'heizen')).toBe(-0.5);
		// Kühlen: mehr Leistung heißt tieferer Sollwert
		expect(plannedOffsetK(1, 'kuehlen')).toBe(-0.5);
		expect(plannedOffsetK(-2, 'kuehlen')).toBe(1);
	});

	it('kennt ohne Stufen oder ohne Heizen und Kühlen keinen Versatz', () => {
		expect(plannedOffsetK(null, 'heizen')).toBeNull();
		expect(plannedOffsetK(undefined, 'kuehlen')).toBeNull();
		expect(plannedOffsetK(1, 'unbekannt')).toBeNull();
		expect(plannedOffsetK(1, null)).toBeNull();
	});

	it('gibt bei 0 Stufen nie „-0“', () => {
		expect(Object.is(plannedOffsetK(0, 'kuehlen'), 0)).toBe(true);
	});
});

describe('thermalModeView', () => {
	it('zeigt Kopf, Grundleistung, Zeitkonstante, Speicher mit Ereignissen, Phasen und Anpassung', () => {
		const view = thermalModeView(HEATING);
		expect(view.head).toBe('Heizen · 21 Tage');
		expect(view.state).toBe('Modell taugt.');
		expect(view.usable).toBe(true);
		expect(rows(HEATING)).toEqual({
			Grundleistung: `82${NB}W je K Temperaturdifferenz`,
			Zeitkonstante: `38${NB}h`,
			Speicher: `2,4${NB}kWh je K (aus 4 Versatz-Ereignissen, Modell 3,1)`,
			'Auskühl-Phasen': '9',
			Anpassung: 'R² 0,81'
		});
	});

	it('zeigt den Speicher ohne Ereignisse als kWh Strom je K', () => {
		const model = { ...HEATING.model!, events: 0, event_error_pct: null, c_kwh_per_k: 3.1 };
		expect(rows({ ...HEATING, model }).Speicher).toBe(`3,1${NB}kWh Strom je K`);
	});

	it('zeigt fehlende Zahlen als Strich und nennt den Grund', () => {
		const view = thermalModeView(COOLING);
		expect(view.head).toBe('Kühlen · 6 Tage');
		expect(view.usable).toBe(false);
		expect(view.state).toBe('Noch zu wenig Tage mit Kühlen: 6 von 14.');
		expect(rows(COOLING)).toMatchObject({
			Zeitkonstante: '–',
			Speicher: '–',
			'Auskühl-Phasen': '0',
			Anpassung: 'R² 0,38'
		});
	});

	it('hat ohne Stand keine Zeilen und nur die Betriebsart im Kopf', () => {
		const empty: ThermalModeStatus = {
			mode: 'kuehlen',
			model: null,
			usable: false,
			reason: 'no_data',
			text: 'Noch keine Daten der Klimaanlage.',
			computed_at: null
		};
		const view = thermalModeView(empty);
		expect(view.head).toBe('Kühlen');
		expect(view.rows).toEqual([]);
		expect(view.state).toBe('Noch keine Daten der Klimaanlage.');
	});
});

describe('computedText', () => {
	it('nennt Tag und Uhrzeit in Wien', () => {
		expect(computedText('2026-10-09T01:50:00+00:00')).toBe('Berechnet am 09.10. 03:50.');
		expect(computedText(null)).toBeNull();
	});
});

describe('planThermalLine', () => {
	const preview: PlanThermal = {
		device: 'klima',
		mode: 'heizen',
		active: false,
		saving_ct: 12,
		model_computed_at: null,
		reason: 'ok'
	};

	it('zeigt die Vorschau und die aktive Verschiebung mit der Ersparnis', () => {
		expect(planThermalLine(preview)).toBe(
			`Vorschau: Vorheizen/Vorkühlen würde etwa 12${NB}ct sparen.`
		);
		expect(planThermalLine({ ...preview, active: true, saving_ct: 18.4 })).toBe(
			`Vorheizen/Vorkühlen aktiv: spart etwa 18,4${NB}ct.`
		);
	});

	it('nennt sonst den Grund', () => {
		const line = (changes: Partial<PlanThermal>) => planThermalLine({ ...preview, ...changes });
		expect(line({ reason: 'off' })).toBe('Vorheizen und Vorkühlen ist aus.');
		expect(line({ reason: 'no_model', mode: 'kuehlen' })).toBe(
			'Kein taugliches Wärmemodell für Kühlen.'
		);
		expect(line({ reason: 'mode', mode: 'unbekannt' })).toBe(
			'Die Klimaanlage heizt oder kühlt gerade nicht.'
		);
		expect(line({ reason: 'not_worth' })).toBe('Verschieben lohnt sich in diesem Plan nicht.');
		expect(line({ reason: 'solver' })).toBe('Die Verschiebung ließ sich nicht berechnen.');
	});
});

describe('thermalRuns', () => {
	function slots(steps: (number | null)[]): PlanSlot[] {
		const times = ['10:00', '10:15', '10:30', '10:45', '11:00', '11:15'];
		return steps.map((value, index) =>
			slot(times[index]!, 'EIGENVERBRAUCH', 0, 'EIGENVERBRAUCH', { thermal_steps: value })
		);
	}

	it('fasst gleiche Stufen zu Zeilen zusammen, 0 als Strich', () => {
		expect(thermalRuns(slots([0, 1, 1, 2, 0, -1]), 'heizen')).toEqual([
			{ start: '2026-10-09T08:00:00+00:00', end: '2026-10-09T08:15:00.000Z', shift: '–' },
			{ start: '2026-10-09T08:15:00+00:00', end: '2026-10-09T08:45:00.000Z', shift: `+0,5${NB}K` },
			{ start: '2026-10-09T08:45:00+00:00', end: '2026-10-09T09:00:00.000Z', shift: `+1,0${NB}K` },
			{ start: '2026-10-09T09:00:00+00:00', end: '2026-10-09T09:15:00.000Z', shift: '–' },
			{
				start: '2026-10-09T09:15:00+00:00',
				end: '2026-10-09T09:30:00.000Z',
				shift: `${MINUS}0,5${NB}K`
			}
		]);
	});

	it('dreht beim Kühlen das Vorzeichen um', () => {
		expect(thermalRuns(slots([1, 1]), 'kuehlen').map((run) => run.shift)).toEqual([
			`${MINUS}0,5${NB}K`
		]);
	});

	it('hat ohne Verschiebung keine Zeilen', () => {
		expect(thermalRuns(slots([null, null, null]), 'heizen')).toEqual([]);
		expect(thermalRuns([], 'heizen')).toEqual([]);
	});
});

describe('plannedUntil', () => {
	const plan = {
		valid_until: '2026-10-09T12:00:00+00:00',
		slots: [
			{ start: '2026-10-09T08:00:00+00:00', thermal_steps: 0 },
			{ start: '2026-10-09T08:15:00+00:00', thermal_steps: 1 },
			{ start: '2026-10-09T08:30:00+00:00', thermal_steps: 1 },
			{ start: '2026-10-09T08:45:00+00:00', thermal_steps: 2 }
		]
	};

	it('endet mit dem letzten Slot gleicher Stufen ab dem laufenden', () => {
		expect(plannedUntil(plan, new Date('2026-10-09T08:20:00Z'))).toBe('2026-10-09T08:45:00.000Z');
		expect(plannedUntil(plan, new Date('2026-10-09T08:50:00Z'))).toBe('2026-10-09T09:00:00.000Z');
	});

	it('endet spätestens, wenn der Plan nicht mehr gilt', () => {
		const short = { ...plan, valid_until: '2026-10-09T08:40:00+00:00' };
		expect(plannedUntil(short, new Date('2026-10-09T08:20:00Z'))).toBe('2026-10-09T08:40:00.000Z');
	});

	it('weiß ohne Plan oder ohne laufenden Slot kein Ende', () => {
		expect(plannedUntil(null, new Date('2026-10-09T08:20:00Z'))).toBeNull();
		expect(plannedUntil(plan, new Date('2026-10-09T07:59:00Z'))).toBeNull();
		expect(plannedUntil(plan, new Date('2026-10-09T09:00:00Z'))).toBeNull();
	});
});
