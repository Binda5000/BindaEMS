import { describe, expect, it } from 'vitest';
import { derived } from '$lib/testing/fixtures';
import { balance } from './balance';

const labels = { evcs: 'EVCS', twc: 'Garage' };

function ids(segments: { id: string }[]): string[] {
	return segments.map((segment) => segment.id);
}

describe('balance', () => {
	it('stellt Quellen und Verbrauch gegenüber, Ruhendes fehlt', () => {
		const result = balance(
			derived({
				pv_total_w: 3230,
				grid_w: 380,
				battery_w: 1000,
				house_load_w: 1230,
				wallbox_w: { evcs: 0, twc: 1380 }
			}),
			labels
		);
		expect(ids(result.sources)).toEqual(['pv', 'grid']);
		expect(result.sourcesW).toBe(3610);
		expect(result.sources[0]?.share).toBeCloseTo(0.895, 3);
		expect(result.sources[1]?.share).toBeCloseTo(0.105, 3);
		expect(result.sources.map((s) => s.label)).toEqual(['PV', 'Netz']);

		expect(ids(result.sinks)).toEqual(['house', 'battery', 'wallbox:twc']);
		expect(result.sinks.map((s) => s.label)).toEqual(['Haus', 'Akku', 'Garage']);
		expect(result.sinks.map((s) => s.kind)).toEqual(['house', 'battery', 'wallbox']);
		expect(result.sinks.map((s) => s.powerW)).toEqual([1230, 1000, 1380]);
		expect(result.sinksW).toBe(3610);
	});

	it('mit Einspeisung und Entladen: Akku ist Quelle, Netz geht in die Einspeisung', () => {
		const result = balance(
			derived({
				pv_total_w: 0,
				grid_w: -900,
				battery_w: -500,
				house_load_w: 400,
				wallbox_w: { evcs: 0, twc: 0 }
			}),
			labels
		);
		expect(ids(result.sources)).toEqual(['battery']);
		expect(ids(result.sinks)).toEqual(['house', 'export']);
		expect(result.sinks[1]).toMatchObject({ label: 'Einspeisung', kind: 'export', powerW: 900 });
		for (const side of [result.sources, result.sinks]) {
			expect(side.every((segment) => segment.share >= 0)).toBe(true);
			expect(side.reduce((sum, segment) => sum + segment.share, 0)).toBeCloseTo(1, 10);
		}
	});

	it('ohne Werte bleiben die Listen leer und die Summen fehlen', () => {
		expect(balance(null, labels)).toEqual({
			sources: [],
			sinks: [],
			sourcesW: null,
			sinksW: null
		});
	});

	it('kennt unbekannte Werte nicht als 0', () => {
		const result = balance(
			derived({
				pv_total_w: null,
				grid_w: null,
				battery_w: null,
				house_load_w: null,
				wallbox_w: {}
			}),
			{}
		);
		expect(result).toEqual({ sources: [], sinks: [], sourcesW: null, sinksW: null });
	});

	it('Ruhe überall ergibt Summen von 0 ohne Segmente', () => {
		const result = balance(
			derived({
				pv_total_w: 5,
				grid_w: 0,
				battery_w: 0,
				house_load_w: 10,
				wallbox_w: { twc: 0 }
			}),
			labels
		);
		expect(result).toEqual({ sources: [], sinks: [], sourcesW: 0, sinksW: 0 });
	});

	it('Wallbox ohne Namen erscheint mit ihrem Schlüssel', () => {
		const result = balance(derived({ wallbox_w: { mystery: 2000 } }), {});
		expect(result.sinks.find((s) => s.id === 'wallbox:mystery')?.label).toBe('mystery');
	});

	it('Messfehler gegen die Richtung von PV, Haus und Wallbox zählen nicht', () => {
		const result = balance(
			derived({
				pv_total_w: -300,
				grid_w: 500,
				battery_w: 0,
				house_load_w: -100,
				wallbox_w: { twc: -50 }
			}),
			labels
		);
		expect(ids(result.sources)).toEqual(['grid']);
		expect(result.sinks).toEqual([]);
	});
});
