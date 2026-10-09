// Alle Endpunkte der API (Plan 1c, Anhang A) mit geprüften Antworten
import * as v from 'valibot';
import { request } from './client';
import * as s from './schemas';

export interface Load {
	background?: boolean;
	signal?: AbortSignal;
}

type Range = { from: string; to: string };

export const EXPORT_SETTINGS_URL = '/api/settings/export';

const RepriceSchema = v.object({ repriced: v.number() });

function get<T>(
	path: string,
	schema: v.GenericSchema<unknown, T>,
	options: Load & { redirectOn401?: boolean } = {}
): Promise<T> {
	const { background, signal, redirectOn401 } = options;
	return request('GET', path, { schema, background, signal, redirectOn401 });
}

function withQuery(path: string, params: Record<string, string>): string {
	return `${path}?${new URLSearchParams(params)}`;
}

export const api = {
	me: (o?: Load & { redirectOn401?: boolean }): Promise<s.MeResponse> =>
		get('/api/auth/me', s.MeResponseSchema, o),
	login: (body: s.LoginBody): Promise<s.MeResponse> =>
		request('POST', '/api/auth/login', { body, schema: s.MeResponseSchema, redirectOn401: false }),
	logout: (): Promise<void> => request('POST', '/api/auth/logout'),
	changePassword: (oldPassword: string, newPassword: string): Promise<void> =>
		request('POST', '/api/auth/password', {
			body: { old_password: oldPassword, new_password: newPassword }
		}),
	totpSetup: (password: string): Promise<s.TotpSetup> =>
		request('POST', '/api/auth/totp/setup', { body: { password }, schema: s.TotpSetupSchema }),
	totpEnable: (code: string): Promise<void> =>
		request('POST', '/api/auth/totp/enable', { body: { code } }),
	totpDisable: (password: string): Promise<void> =>
		request('POST', '/api/auth/totp/disable', { body: { password } }),

	users: (o?: Load): Promise<s.User[]> => get('/api/users', v.array(s.UserSchema), o),
	createUser: (body: s.NewUser): Promise<s.User> =>
		request('POST', '/api/users', { body, schema: s.UserSchema }),
	updateUser: (id: number, body: s.UserPatch): Promise<s.User> =>
		request('PATCH', `/api/users/${id}`, { body, schema: s.UserSchema }),
	deleteUser: (id: number): Promise<void> => request('DELETE', `/api/users/${id}`),
	audit: (limit: number, o?: Load): Promise<s.AuditEntry[]> =>
		get(withQuery('/api/audit', { limit: String(limit) }), v.array(s.AuditEntrySchema), o),

	health: (o?: Load): Promise<s.Health> => get('/health', s.HealthSchema, o),
	system: (o?: Load): Promise<s.SystemResponse> => get('/api/system', s.SystemResponseSchema, o),

	settings: (o?: Load): Promise<s.SettingsCurrent> =>
		get('/api/settings', s.SettingsCurrentSchema, o),
	settingsVersions: (o?: Load): Promise<s.SettingsMeta[]> =>
		get('/api/settings/versions', v.array(s.SettingsMetaSchema), o),
	saveSettings: (
		baseVersion: number,
		settings: s.RuntimeSettings,
		comment: string | null
	): Promise<s.SettingsCurrent> =>
		request('PUT', '/api/settings', {
			body: { base_version: baseVersion, settings, comment },
			schema: s.SettingsCurrentSchema
		}),
	importSettings: (yaml: string): Promise<s.SettingsCurrent> =>
		request('POST', '/api/settings/import', { body: { yaml }, schema: s.SettingsCurrentSchema }),
	limits: (o?: Load): Promise<s.Limits> => get('/api/limits', s.LimitsSchema, o),

	consumers: (o?: Load): Promise<s.ConsumersResponse> =>
		get('/api/consumers', s.ConsumersResponseSchema, o),
	consumerCandidates: (o?: Load): Promise<s.Candidates> =>
		get('/api/consumers/candidates', s.CandidatesSchema, o),
	createConsumer: (input: s.ConsumerInput): Promise<s.Consumer> =>
		request('POST', '/api/consumers', { body: input, schema: s.ConsumerSchema }),
	updateConsumer: (id: number, input: s.ConsumerInput): Promise<s.Consumer> =>
		request('PATCH', `/api/consumers/${id}`, { body: input, schema: s.ConsumerSchema }),
	deleteConsumer: (id: number): Promise<void> => request('DELETE', `/api/consumers/${id}`),

	historyCatalog: (o?: Load): Promise<s.CatalogEntry[]> =>
		get('/api/history/catalog', v.array(s.CatalogEntrySchema), o),
	history: (series: string[], range: Range, o?: Load): Promise<s.HistoryResponse> =>
		get(
			withQuery('/api/history', { series: series.join(','), from: range.from, to: range.to }),
			s.HistoryResponseSchema,
			o
		),

	prices: (range: Range | null, o?: Load): Promise<s.PricesResponse> =>
		get(range ? withQuery('/api/prices', range) : '/api/prices', s.PricesResponseSchema, o),
	pricesNow: (o?: Load): Promise<s.PricesNow> => get('/api/prices/now', s.PricesNowSchema, o),
	refreshPrices: (): Promise<s.PriceStatus> =>
		request('POST', '/api/prices/refresh', { schema: s.PriceStatusSchema }),
	forecast: (o?: Load): Promise<s.Forecast> => get('/api/forecast/pv', s.ForecastSchema, o),
	ledgerDays: (first: string, last: string, o?: Load): Promise<s.DaySummary[]> =>
		get(withQuery('/api/ledger/days', { from: first, to: last }), v.array(s.DaySummarySchema), o),
	reprice: (first: string, last: string): Promise<{ repriced: number }> =>
		request('POST', '/api/ledger/reprice', {
			body: { from: first, to: last },
			schema: RepriceSchema
		})
};
