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
