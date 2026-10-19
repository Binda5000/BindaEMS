import { expect, it } from 'vitest';
import type { PriceStatus } from '$lib/api/schemas';
import { coreState, reading, system } from '$lib/testing/fixtures';
import {
	actuatorModeLabel,
	actuatorNotes,
	heartbeatLabel,
	liveConditions,
	needsEvccConfirmation,
	modeLabel,
	selfcheckLabel,
	signalRows,
	vatText,
	watchdogStateLabel,
	watchdogVersionText
} from './view';

it('benennt Selbstprüfungen deutsch', () => {
	expect(
		['dess', 'multi_mode', 'import_limit', 'evcs_mode.twc', 'neu'].map(selfcheckLabel)
	).toEqual(['Dynamic ESS', 'Multi-Modus', 'Importlimit', 'EVCS-Modus twc', 'neu']);
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

function demoHealth() {
	const { health } = system().core;
	if (health === null) throw new Error('system.json enthält keinen Gesundheitsbericht');
	return health;
}

function actuator(id: string) {
	const found = demoHealth().actuators.find((item) => item.id === id);
	if (found === undefined) throw new Error(`Aktor ${id} fehlt in system.json`);
	return found;
}

it('liveConditions nennt die vier Bedingungen, den Watchdog nur für Victron und EVCS', () => {
	const health = demoHealth();
	const victron = { ...actuator('victron'), live_allowed: true };
	expect(liveConditions(victron, health, { control: true })).toEqual([
		{ label: 'live erlaubt', met: true },
		{ label: 'Watchdog scharf', met: false },
		{ label: 'Regelbetrieb an', met: true },
		{ label: 'nicht gesperrt', met: true }
	]);
	expect(
		liveConditions(actuator('vehicle.tesla'), health, { control: true }).map((c) => c.label)
	).toEqual(['live erlaubt', 'Regelbetrieb an', 'nicht gesperrt']);
});

it('liveConditions wertet Freigabe, Watchdog, Regelbetrieb und Sperre aus', () => {
	const health = demoHealth();
	const armed = { ...health, watchdog: { ...health.watchdog, armed: true } };
	const evcs = {
		...actuator('wallbox.evcs'),
		live_allowed: true,
		locked: ['Dynamic ESS ist aktiv.']
	};
	expect(liveConditions(evcs, armed, { control: false })).toEqual([
		{ label: 'live erlaubt', met: true },
		{ label: 'Watchdog scharf', met: true },
		{ label: 'Regelbetrieb an', met: false },
		{ label: 'nicht gesperrt', met: false }
	]);
	// ohne Absicht regelt der core nichts
	expect(liveConditions(evcs, armed, null)[2]).toEqual({ label: 'Regelbetrieb an', met: false });
	expect(liveConditions(actuator('victron'), health, null)[0]).toEqual({
		label: 'live erlaubt',
		met: false
	});
});

it('needsEvccConfirmation gilt für EVCS und Tesla, nicht für Victron', () => {
	expect(needsEvccConfirmation(['victron'])).toBe(false);
	expect(needsEvccConfirmation([])).toBe(false);
	expect(needsEvccConfirmation(['victron', 'wallbox.evcs'])).toBe(true);
	expect(needsEvccConfirmation(['vehicle.tesla'])).toBe(true);
});

it('needsEvccConfirmation hängt nicht an den Schlüsseln der Demo', () => {
	// die Schlüssel aus wallboxes und vehicles wählt die Konfiguration
	expect(needsEvccConfirmation(['wallbox.garage'])).toBe(true);
	expect(needsEvccConfirmation(['vehicle.egolf'])).toBe(true);
	// Ruling R29: ein unbekannter Aktor verlangt im Zweifel die Bestätigung (eine künftige
	// Familie muss sich ausdrücklich ausnehmen)
	expect(needsEvccConfirmation(['neu'])).toBe(true);
	expect(needsEvccConfirmation(['heatpump.keller'])).toBe(true);
	// nur die Klimaanlage ist als ganze Familie ausgenommen, nicht ein ähnlicher Name
	expect(needsEvccConfirmation(['thermal'])).toBe(true);
	expect(needsEvccConfirmation(['thermalx.klima'])).toBe(true);
});

it('needsEvccConfirmation gilt nicht für die Klimaanlage', () => {
	expect(needsEvccConfirmation(['thermal.klima'])).toBe(false);
	expect(needsEvccConfirmation(['victron', 'thermal.klima'])).toBe(false);
	expect(needsEvccConfirmation(['thermal.klima', 'wallbox.evcs'])).toBe(true);
});
