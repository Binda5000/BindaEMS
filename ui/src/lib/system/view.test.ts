import { expect, it } from 'vitest';
import type { PriceStatus } from '$lib/api/schemas';
import { coreState, reading } from '$lib/testing/fixtures';
import {
	actuatorModeLabel,
	actuatorNotes,
	heartbeatLabel,
	modeLabel,
	selfcheckLabel,
	signalRows,
	vatText,
	watchdogStateLabel,
	watchdogVersionText
} from './view';

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

it('benennt Betriebsart, Watchdog und Heartbeat', () => {
	expect(modeLabel('DEGRADED')).toBe('Eingeschränkt');
	expect(watchdogStateLabel('armed')).toBe('scharf');
	expect(heartbeatLabel('released')).toBe('freigegeben');
});

it('benennt jeden Zustand und jede Betriebsart', () => {
	expect((['START', 'OBSERVE', 'CONTROL', 'DEGRADED', 'SAFE'] as const).map(modeLabel)).toEqual([
		'Start',
		'Beobachten (nur lesend)',
		'Regeln',
		'Eingeschränkt',
		'Sicherer Zustand'
	]);
	expect(
		(['waiting', 'armed', 'tripped', 'disabled', 'unknown'] as const).map(watchdogStateLabel)
	).toEqual(['wartet', 'scharf', 'ausgelöst', 'deaktiviert', 'keine Antwort']);
	expect((['off', 'active', 'released'] as const).map(heartbeatLabel)).toEqual([
		'aus (kein Aktor live)',
		'aktiv',
		'freigegeben'
	]);
	expect((['dry_run', 'live'] as const).map(actuatorModeLabel)).toEqual(['Dry-Run', 'live']);
});

it('zeigt eine abweichende Watchdog-Version mit der erwarteten', () => {
	expect(watchdogVersionText('0.9.0', '1.0.0')).toBe('0.9.0 (erwartet 1.0.0)');
	expect(watchdogVersionText('1.0.0', '1.0.0')).toBe('1.0.0');
	expect(watchdogVersionText(null, '1.0.0')).toBe('–');
});

it('sammelt Gründe und Sperren je Aktor', () => {
	expect(
		actuatorNotes({
			reasons: ['Watchdog am Cerbo ist nicht scharf.'],
			locked: ['Dynamic ESS ist aktiv (Modus 1).']
		} as never)
	).toEqual(['Watchdog am Cerbo ist nicht scharf.', 'gesperrt: Dynamic ESS ist aktiv (Modus 1).']);
});

it('nennt Sperren einzeln mit Präfix und lässt einen freien Aktor ohne Hinweise', () => {
	expect(
		actuatorNotes({ reasons: [], locked: ['EVCS-Modus nicht verfügbar.', 'Netzausfall.'] })
	).toEqual(['gesperrt: EVCS-Modus nicht verfügbar.', 'gesperrt: Netzausfall.']);
	expect(actuatorNotes({ reasons: [], locked: [] })).toEqual([]);
});
