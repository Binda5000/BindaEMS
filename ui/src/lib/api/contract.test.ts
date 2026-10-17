import * as v from 'valibot';
import { describe, expect, it } from 'vitest';
import * as s from './schemas';

const files: Record<string, unknown> = import.meta.glob('./contract/*.json', {
	eager: true,
	import: 'default'
});

const SCHEMAS: Record<string, v.GenericSchema> = {
	me: s.MeResponseSchema,
	state: s.StateResponseSchema,
	system: s.SystemResponseSchema,
	settings: s.SettingsCurrentSchema,
	'settings-versions': v.array(s.SettingsMetaSchema),
	limits: s.LimitsSchema,
	consumers: s.ConsumersResponseSchema,
	'consumer-candidates': s.CandidatesSchema,
	'history-catalog': v.array(s.CatalogEntrySchema),
	history: s.HistoryResponseSchema,
	prices: s.PricesResponseSchema,
	'prices-now': s.PricesNowSchema,
	forecast: s.ForecastSchema,
	'forecast-load': s.LoadForecastSchema,
	'forecast-quality': s.QualitySchema,
	'ledger-days': v.array(s.DaySummarySchema),
	users: v.array(s.UserSchema),
	audit: v.array(s.AuditEntrySchema),
	'live-hello': s.LiveMessageSchema,
	'live-state': s.LiveMessageSchema,
	'live-core': s.LiveMessageSchema,
	'live-alarm': s.LiveMessageSchema, // je Alarmstufe des core ein Alarm
	health: s.HealthSchema,
	'totp-setup': s.TotpSetupSchema,
	reprice: v.object({ repriced: v.number() }),
	control: s.ControlResponseSchema,
	'control-reply': s.ControlReplySchema,
	'control-support': s.ControlReplySchema,
	decisions: s.DecisionsResponseSchema,
	'decisions-latest': s.LatestDecisionsResponseSchema,
	plan: s.PlanResponseSchema,
	'plan-grid-charge': s.GridChargeReplySchema,
	'error-422': s.ValidationErrorSchema
};

function issues(schema: v.GenericSchema, value: unknown): string[] {
	const result = v.safeParse(schema, value);
	return result.issues?.map((issue) => `${v.getDotPath(issue)}: ${issue.message}`) ?? [];
}

it('jede Vertragsdatei hat ein Schema', () => {
	const names = Object.keys(files).map((path) => path.slice('./contract/'.length, -'.json'.length));
	expect(names.sort()).toEqual(Object.keys(SCHEMAS).sort());
});

describe.each(Object.entries(SCHEMAS))('%s', (name, schema) => {
	it('passt zum Schema', () => {
		expect(issues(schema, files[`./contract/${name}.json`])).toEqual([]);
	});
});

it('akzeptiert die Nullfälle der API', () => {
	expect(
		issues(s.StateResponseSchema, {
			core_connected: false,
			updated_at: null,
			state: null,
			alarms: []
		})
	).toEqual([]);
	expect(issues(s.PricesNowSchema, { now: null, next_3h: [] })).toEqual([]);
	expect(
		issues(s.SystemResponseSchema, {
			core: { connected: false, health: null },
			components: [],
			warnings: []
		})
	).toEqual([]);
});

describe('Gesundheitsbericht des core', () => {
	const demo = files['./contract/system.json'] as { core: { health: Record<string, unknown> } };
	const check = (changes: Record<string, unknown>) =>
		issues(s.CoreHealthSchema, { ...demo.core.health, ...changes });
	const watchdog = (changes: Record<string, unknown>) =>
		check({ watchdog: { ...(demo.core.health.watchdog as object), ...changes } });

	it.each(['START', 'OBSERVE', 'CONTROL', 'DEGRADED', 'SAFE'])(
		'akzeptiert den Betriebszustand %s',
		(mode) => {
			expect(check({ mode })).toEqual([]);
		}
	);

	it('lehnt einen unbekannten Betriebszustand ab', () => {
		expect(check({ mode: 'RUNNING' })).not.toEqual([]);
	});

	it.each(['waiting', 'armed', 'tripped', 'disabled', 'unknown'])(
		'akzeptiert den Watchdog-Zustand %s',
		(state) => {
			expect(watchdog({ state })).toEqual([]);
		}
	);

	it.each(['off', 'active', 'released'])('akzeptiert den Heartbeat %s', (heartbeat) => {
		expect(watchdog({ heartbeat })).toEqual([]);
	});

	it('lehnt einen unbekannten Watchdog-Zustand und Heartbeat ab', () => {
		expect(watchdog({ state: 'armd' })).not.toEqual([]);
		expect(watchdog({ heartbeat: 'on' })).not.toEqual([]);
	});

	it('akzeptiert einen Watchdog ohne Antwort mit unbekannten Werten', () => {
		const unknown = {
			state: 'unknown',
			trip_count: null,
			last_trip: null,
			last_trip_failures: null,
			version: null
		};
		expect(watchdog(unknown)).toEqual([]);
	});

	it('akzeptiert Absicht, Betriebsarten der Aktoren und den Beginn der Betriebsart', () => {
		const intent = {
			version: 3,
			control: true,
			actuators: { victron: 'live', 'wallbox.evcs': 'dry_run' }
		};
		expect(check({ intent, mode_since: null })).toEqual([]);
		expect(check({ intent: { ...intent, actuators: { victron: 'manual' } } })).not.toEqual([]);
		const actuator = {
			id: 'victron',
			label: 'Victron (ESS)',
			live_allowed: true,
			reasons: [],
			locked: [],
			watchdog_needed: true
		};
		expect(
			check({ actuators: [{ ...actuator, requested: 'live', effective: 'dry_run' }] })
		).toEqual([]);
		expect(
			check({ actuators: [{ ...actuator, requested: 'live', effective: 'auto' }] })
		).not.toEqual([]);
	});
});

it('Gesundheitsbericht nennt Fahrzeuge, Wallboxen und den scharfen Watchdog', () => {
	const health = v.parse(s.SystemResponseSchema, files['./contract/system.json']).core.health!;
	expect(health.watchdog.armed).toBe(false);
	expect(health.wallboxes.map((w) => w.id)).toEqual(['evcs', 'twc']);
	expect(health.actuators.find((a) => a.id === 'vehicle.tesla')?.watchdog_needed).toBe(false);
	expect(health.vehicles.map((x) => [x.id, x.commands_hour])).toEqual([
		['tesla', 0],
		['egolf', null]
	]);
	expect(health.intent_version).toBe(1);
});

describe('Bedienung und Entscheidungen', () => {
	const decisions = files['./contract/decisions.json'] as {
		decisions: Record<string, unknown>[];
	};
	const first = decisions.decisions[0];

	it('akzeptiert jedes Ergebnis einer Entscheidung, auch von außen geändert', () => {
		for (const result of [
			'ok',
			'error',
			'timeout',
			'not_sent',
			'dry_run',
			'rejected',
			'deferred',
			'foreign'
		]) {
			expect(issues(s.DecisionSchema, { ...first, result })).toEqual([]);
		}
		expect(issues(s.DecisionSchema, { ...first, result: 'unbekannt' })).not.toEqual([]);
	});

	it('Entscheidungen des core haben keinen Cursor, die der app schon', () => {
		const { cursor, ...bare } = first;
		expect(cursor).toBe(1);
		expect(issues(s.DecisionSchema, bare)).toEqual([]);
		expect(issues(s.DecisionSchema, first)).toEqual([]);
	});

	it('akzeptiert eine Antwort ohne Absicht und ohne core', () => {
		expect(issues(s.ControlResponseSchema, { core_connected: false, intent: null })).toEqual([]);
	});

	it('lehnt einen unbekannten Lademodus ab', () => {
		expect(issues(s.ChargeModeSchema, 'sofort')).not.toEqual([]);
		expect(issues(s.ChargeModeSchema, 'min_pv')).toEqual([]);
	});

	it('kennt die Antwort der Quittierung', () => {
		expect(issues(s.AckResponseSchema, { detail: 'Quittiert.' })).toEqual([]);
	});
});

describe('Zeitplan', () => {
	const demo = files['./contract/plan.json'] as { plan: Record<string, unknown> };

	it('akzeptiert die Antwort ohne Plan', () => {
		expect(
			issues(s.PlanResponseSchema, {
				status: { ok: false, message: 'Noch kein Plan', running: false },
				grid_charge_allowed: false,
				plan: null,
				execution: null
			})
		).toEqual([]);
	});

	it('kennt die sieben Stände der Ausführung', () => {
		const response = (state: string) => ({
			...(files['./contract/plan.json'] as object),
			execution: { state, text: 'Satz' }
		});
		for (const state of ['unreachable', 'pending', 'off', 'idle', 'locked', 'dry_run', 'live']) {
			expect(issues(s.PlanResponseSchema, response(state))).toEqual([]);
		}
		expect(issues(s.PlanResponseSchema, response('none'))).not.toEqual([]);
	});

	it('akzeptiert einen Ersatzplan mit Grund', () => {
		const plan = {
			...demo.plan,
			source: 'fallback',
			solver_status: 'timeout',
			fallback_reason: 'Solver nach 30 s abgebrochen'
		};
		expect(issues(s.PlanSchema, plan)).toEqual([]);
	});

	it('lehnt eine unbekannte Absicht ab', () => {
		const slots = demo.plan.slots as Record<string, unknown>[];
		expect(issues(s.PlanSlotSchema, { ...slots[0], intent: 'LADEN' })).not.toEqual([]);
	});
});
