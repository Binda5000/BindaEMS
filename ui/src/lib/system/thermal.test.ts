import { describe, expect, it } from 'vitest';
import type { ThermalHealth } from '$lib/api/schemas';
import { formatDateTime } from '$lib/format';
import { thermalLoad, thermalOffset, thermalRows } from './thermal';

const NB = ' ';
const MINUS = '-';

const base: ThermalHealth = {
	label: 'Klimaanlage',
	connected: true,
	last_packet_at: '2026-10-09T08:00:00+00:00',
	foreign_packets: 0,
	bad_packets: 0,
	unit_mismatch: 0,
	mode: 'kuehlen',
	running: true,
	actual_c: 24.5,
	setpoint_c: 23.5,
	base_setpoint_c: 24.5,
	offset_sent_k: -1,
	offset_active_k: -1,
	wish: 2,
	power_w: 1250,
	reason: 'thermal_more',
	hint: 'neutral',
	base_w: 400,
	spare_w: 500
};

function rows(changes: Partial<ThermalHealth> = {}): Record<string, string> {
	return Object.fromEntries(thermalRows({ ...base, ...changes }).map((r) => [r.label, r.value]));
}

describe('thermalLoad', () => {
	it('nennt Grundlast und freigebbare Leistung', () => {
		expect(thermalLoad(base)).toBe(`Grundlast 400${NB}W · freigebbar 500${NB}W`);
	});

	it('zeigt „–“ ohne Wert', () => {
		expect(thermalLoad({ ...base, base_w: null, spare_w: null })).toBe(
			'Grundlast – · freigebbar –'
		);
		expect(thermalLoad({ ...base, spare_w: 0 })).toBe(`Grundlast 400${NB}W · freigebbar 0${NB}W`);
	});
});

describe('thermalRows', () => {
	it('zeigt Kühlen mit bestätigtem Versatz', () => {
		expect(thermalRows(base)).toEqual([
			{ label: 'Betriebsart', value: 'Kühlen' },
			{ label: 'Läuft', value: 'ja' },
			{ label: 'Ist', value: `24,5${NB}°C` },
			{ label: 'Soll ohne Versatz', value: `24,5${NB}°C` },
			{ label: 'Versatz', value: `${MINUS}1,0${NB}K (bestätigt)` },
			{ label: 'Wunsch', value: 'Stufe +2' },
			{ label: 'Leistung', value: `1,25${NB}kW` },
			{ label: 'Letzter Empfang', value: formatDateTime(base.last_packet_at!) },
			{ label: 'Grund', value: 'PV-Überschuss in die Klimaanlage' }
		]);
	});

	it('nennt das abweichende Echo', () => {
		expect(rows({ offset_active_k: 0 }).Versatz).toBe(`${MINUS}1,0${NB}K (Echo +0,0${NB}K)`);
		expect(rows({ offset_sent_k: 0.5, offset_active_k: 0 }).Versatz).toBe(
			`+0,5${NB}K (Echo +0,0${NB}K)`
		);
	});

	it('bestätigt das Echo auf 0,05 K genau', () => {
		expect(rows({ offset_sent_k: 0.5, offset_active_k: 0.54 }).Versatz).toBe(
			`+0,5${NB}K (bestätigt)`
		);
		expect(rows({ offset_sent_k: 0.5, offset_active_k: 0.56 }).Versatz).toContain('Echo');
		expect(rows({ offset_sent_k: 0, offset_active_k: 0 }).Versatz).toBe(`+0,0${NB}K (bestätigt)`);
	});

	it('zeigt unbekannte Werte als Strich', () => {
		const unknown = rows({
			running: null,
			actual_c: null,
			base_setpoint_c: null,
			offset_sent_k: null,
			offset_active_k: null,
			power_w: null
		});
		expect(unknown.Läuft).toBe('–');
		expect(unknown.Ist).toBe('–');
		expect(unknown['Soll ohne Versatz']).toBe('–');
		expect(unknown.Versatz).toBe('–');
		expect(unknown.Leistung).toBe('–');
		expect(rows({ offset_active_k: null }).Versatz).toBe(`${MINUS}1,0${NB}K (Echo –)`);
	});

	it('benennt die Betriebsarten, Unbekanntes bleibt', () => {
		const modes = ['aus', 'heizen', 'lueften', 'kuehlen', 'unbekannt', 'neu'].map(
			(mode) => rows({ mode }).Betriebsart
		);
		expect(modes).toEqual(['Aus', 'Heizen', 'Lüften', 'Kühlen', 'unbekannt', 'neu']);
	});

	it('zeigt negative Wünsche mit Minus und die Null ohne Vorzeichen', () => {
		expect(rows({ wish: -1 }).Wunsch).toBe(`Stufe ${MINUS}1`);
		expect(rows({ wish: 0 }).Wunsch).toBe('Stufe 0');
	});

	it('zeigt ohne frische Werte nur Empfang und Grund', () => {
		const silent = thermalRows({
			...base,
			connected: false,
			last_packet_at: null,
			mode: 'unbekannt',
			running: null,
			wish: 0,
			reason: 'thermal_stale'
		});
		expect(silent).toEqual([
			{ label: 'Letzter Empfang', value: '–' },
			{ label: 'Grund', value: 'Keine Werte der Klimaanlage, Versatz 0' }
		]);
	});

	it('zeigt Wunsch, Grund und Zähler ohne Angabe (Regelung ruht) als Strich', () => {
		const resting = rows({
			wish: null,
			reason: null,
			hint: null,
			foreign_packets: null,
			bad_packets: null,
			unit_mismatch: null,
			mode: null
		});
		expect(resting.Wunsch).toBe('–');
		expect(resting.Grund).toBe('–');
		expect(resting.Betriebsart).toBe('–');
		expect(Object.keys(resting)).not.toContain('Fremde Pakete');
		expect(rows({ wish: null, reason: 'thermal_off', running: true }).Grund).toBe(
			'Klimaanlage aus oder lüftet, Versatz 0'
		);
	});

	it('nennt verworfene Pakete nur, wenn es welche gab', () => {
		expect(Object.keys(rows())).not.toContain('Fremde Pakete');
		const dropped = rows({ foreign_packets: 3, bad_packets: 1, unit_mismatch: 2 });
		expect(dropped['Fremde Pakete']).toBe('3');
		expect(dropped['Fehlerhafte Pakete']).toBe('1');
		expect(dropped['Werte mit falscher Einheit']).toBe('2');
		expect(Object.keys(rows({ unit_mismatch: null }))).not.toContain('Werte mit falscher Einheit');
	});

	it('zeigt „Versatz 0“ statt „aus oder lüftet“, solange die Anlage läuft', () => {
		const text = (changes: Partial<ThermalHealth>) =>
			rows({ reason: 'thermal_off', wish: 0, ...changes }).Grund;
		expect(text({ running: true })).toBe('Versatz 0');
		expect(text({ running: true, mode: 'heizen' })).toBe('Versatz 0');
		expect(text({ running: true, mode: 'unbekannt' })).toBe('Versatz 0');
		// aus oder lüftend stimmt der Text
		expect(text({ running: false, mode: 'aus' })).toBe('Klimaanlage aus oder lüftet, Versatz 0');
		expect(text({ running: true, mode: 'lueften' })).toBe('Klimaanlage aus oder lüftet, Versatz 0');
		expect(text({ running: null })).toBe('Klimaanlage aus oder lüftet, Versatz 0');
		// nur bei Wunsch 0
		expect(text({ running: true, wish: 1 })).toBe('Klimaanlage aus oder lüftet, Versatz 0');
		// andere Gründe bleiben
		expect(rows({ reason: 'thermal_cars', wish: 0, running: true }).Grund).toBe(
			'Autos haben Vorrang'
		);
	});

	it('übersetzt jeden Grund der Strategie', () => {
		const reasons = [
			'thermal_more',
			'thermal_back',
			'thermal_cars',
			'thermal_battery',
			'thermal_less',
			'thermal_peak',
			'thermal_mode',
			'thermal_stale',
			'thermal_no_echo',
			'thermal_off'
		];
		expect(reasons.map((reason) => rows({ reason, running: false }).Grund)).toEqual([
			'PV-Überschuss in die Klimaanlage',
			'Netzbezug, Klimaanlage eine Stufe zurück',
			'Autos haben Vorrang',
			'Akku hat Vorrang',
			'Teurer Strom, Klimaanlage zurückhaltend',
			'Spitze droht, Klimaanlage nicht mehr',
			'Betriebsart gewechselt, Versatz 0',
			'Keine Werte der Klimaanlage, Versatz 0',
			'Klimaanlage nicht steuerbar (kein Echo oder gesperrt), Versatz 0',
			'Klimaanlage aus oder lüftet, Versatz 0'
		]);
		expect(rows({ reason: null }).Grund).toBe('–');
	});
});

describe('thermalOffset', () => {
	it('schreibt Vorzeichen, Komma und eine Nachkommastelle', () => {
		expect(thermalOffset(1.5)).toBe(`+1,5${NB}K`);
		expect(thermalOffset(-0.5)).toBe(`${MINUS}0,5${NB}K`);
		expect(thermalOffset(0)).toBe(`+0,0${NB}K`);
		expect(thermalOffset(-0.04)).toBe(`+0,0${NB}K`);
		expect(thermalOffset(null)).toBe('–');
	});
});
