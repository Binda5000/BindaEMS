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

/**
 * So viele Sessions liefert `GET /api/charging/sessions` höchstens, die neuesten zuerst; die
 * Grenze setzt der Server (`MAX_SESSIONS` in src/bindaems/app/charging/routes.py).
 */
export const SESSIONS_LIMIT = 200;

const StartedSchema = v.object({ started: v.boolean() });
const RepriceSchema = v.object({ repriced: v.number() });

const DECISION_FILTERS = ['since', 'until', 'actuator', 'result', 'text'] as const;

/** Query von `GET /api/decisions`: nur gesetzte Filter; `dry_run=false` blendet Dry-Run aus. */
function decisionParams(query: s.DecisionQuery): Record<string, string> {
	const params: Record<string, string> = {};
	for (const name of DECISION_FILTERS) {
		const value = query[name];
		if (value !== undefined && value !== '') params[name] = value;
	}
	if (query.dry_run !== undefined) params.dry_run = String(query.dry_run);
	if (query.before !== undefined) params.before = String(query.before);
	if (query.limit !== undefined) params.limit = String(query.limit);
	return params;
}

const encode = encodeURIComponent;

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
	learningCandidates: (o?: Load): Promise<s.SensorCandidates> =>
		get('/api/learning/candidates', s.SensorCandidatesSchema, o),
	learningStatus: (o?: Load): Promise<s.LearningStatus> =>
		get('/api/learning/load', s.LearningStatusSchema, o),
	/** Admin: startet das Training im Hintergrund; 409, wenn schon eines läuft */
	trainLoadModel: (): Promise<{ started: boolean }> =>
		request('POST', '/api/learning/load/train', { schema: StartedSchema }),
	drivingStatus: (o?: Load): Promise<s.DrivingStatus> =>
		get('/api/learning/driving', s.DrivingStatusSchema, o),
	/** Admin: rechnet das Fahrverhalten im Hintergrund neu; 409, wenn schon ein Lauf läuft */
	recomputeDriving: (): Promise<{ started: boolean }> =>
		request('POST', '/api/learning/driving/recompute', { schema: StartedSchema }),
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
	loadForecast: (o?: Load): Promise<s.LoadForecast> =>
		get('/api/forecast/load', s.LoadForecastSchema, o),
	forecastQuality: (days = 14, o?: Load): Promise<s.Quality> =>
		get(withQuery('/api/forecast/quality', { days: String(days) }), s.QualitySchema, o),
	forecastQualityDaily: (days = 28, o?: Load): Promise<s.QualityDaily> =>
		get(withQuery('/api/forecast/quality/daily', { days: String(days) }), s.QualityDailySchema, o),
	ledgerDays: (first: string, last: string, o?: Load): Promise<s.DaySummary[]> =>
		get(withQuery('/api/ledger/days', { from: first, to: last }), v.array(s.DaySummarySchema), o),
	/** Statistik des Zeitraums, der `day` (YYYY-MM-DD) enthält; ohne `day` heute */
	stats: (period: s.StatsPeriod, day?: string, o?: Load): Promise<s.Stats> =>
		get(
			withQuery('/api/stats', day === undefined ? { period } : { period, date: day }),
			s.StatsSchema,
			o
		),
	/** Ladesessions der Tage `first` bis `last`, neueste zuerst; `vehicle` ist ein Schlüssel */
	chargingSessions: (
		first: string,
		last: string,
		vehicle?: string,
		o?: Load
	): Promise<s.ChargingSession[]> =>
		get(
			withQuery(
				'/api/charging/sessions',
				vehicle === undefined ? { from: first, to: last } : { from: first, to: last, vehicle }
			),
			v.array(s.ChargingSessionSchema),
			o
		),
	/** Abgleich der Abrechnung mit den Zählern je Tag und Signal, dazu die Summen */
	ledgerChecks: (first: string, last: string, o?: Load): Promise<s.LedgerChecks> =>
		get(withQuery('/api/ledger/checks', { from: first, to: last }), s.LedgerChecksSchema, o),
	reprice: (first: string, last: string): Promise<{ repriced: number }> =>
		request('POST', '/api/ledger/reprice', {
			body: { from: first, to: last },
			schema: RepriceSchema
		}),

	// Bedienung (ab „Bedienen“; Live-Schaltung und Quittierung nur Admin)
	control: (o?: Load): Promise<s.ControlResponse> =>
		get('/api/control', s.ControlResponseSchema, o),
	/** Mehrkosten des Boosts (Leistungspreis im Monat) je Fahrzeug; ab „Lesen“ */
	boostCost: (o?: Load): Promise<s.BoostCost> =>
		get('/api/control/boost-cost', s.BoostCostSchema, o),
	setRegulation: (on: boolean): Promise<s.ControlReply> =>
		request('PUT', '/api/control/regulation', { body: { on }, schema: s.ControlReplySchema }),
	setMode: (vehicle: string, mode: s.ChargeMode): Promise<s.ControlReply> =>
		request('PUT', `/api/control/vehicles/${encode(vehicle)}/mode`, {
			body: { mode },
			schema: s.ControlReplySchema
		}),
	/**
	 * Ziel-SOC (ganze Zahl) und Abfahrt (`HH:MM`) für „Geplant“; `null` lässt das Feld, nie beide.
	 * `departureOn` (`YYYY-MM-DD`) legt die Abfahrt auf diesen Tag; ohne Angabe wird es nicht gesendet.
	 */
	setTarget: (
		vehicle: string,
		targetSocPct: number | null,
		departure: string | null,
		departureOn?: string | null
	): Promise<s.ControlReply> =>
		request('PUT', `/api/control/vehicles/${encode(vehicle)}/target`, {
			body: {
				target_soc_pct: targetSocPct,
				departure,
				...(departureOn === undefined ? {} : { departure_on: departureOn })
			},
			schema: s.ControlReplySchema
		}),
	/** Ab „Bedienen“: schaltet die Automatik für Ziel und Abfahrt; die Antwort ist ihr neuer Zustand */
	setAutomation: (vehicle: string, on: boolean): Promise<s.AutomationState> =>
		request('PUT', `/api/control/vehicles/${encode(vehicle)}/automation`, {
			body: { on },
			schema: s.AutomationStateSchema
		}),
	setBoost: (vehicle: string, on: boolean): Promise<s.ControlReply> =>
		request('PUT', `/api/control/vehicles/${encode(vehicle)}/boost`, {
			body: { on },
			schema: s.ControlReplySchema
		}),
	/** `null`: „Automatisch“, die Übersteuerung dieser Wallbox entfällt */
	setWallboxVehicle: (wallbox: string, vehicle: string | null): Promise<s.ControlReply> =>
		request('PUT', `/api/control/wallboxes/${encode(wallbox)}/vehicle`, {
			body: { vehicle },
			schema: s.ControlReplySchema
		}),
	setGuestMode: (mode: s.ChargeMode): Promise<s.ControlReply> =>
		request('PUT', '/api/control/guest', { body: { mode }, schema: s.ControlReplySchema }),
	/** `socPct` `null`: die Schwelle der Konfiguration */
	setSupport: (allowed: boolean, socPct: number | null): Promise<s.ControlReply> =>
		request('PUT', '/api/control/support', {
			body: { allowed, soc_pct: socPct },
			schema: s.ControlReplySchema
		}),
	setPriority: (priority: string[], surplus: s.SurplusPriority): Promise<s.ControlReply> =>
		request('PUT', '/api/control/priority', {
			body: { priority, surplus_priority: surplus },
			schema: s.ControlReplySchema
		}),
	/** Admin; `password` und `confirmed` nur nötig, wenn ein Aktor auf `live` wechselt */
	setActuators: (
		modes: Record<string, s.ActuatorMode>,
		password: string | null,
		confirmed: boolean
	): Promise<s.ControlReply> =>
		request('PUT', '/api/control/actuators', {
			body: { modes, password, confirmed },
			schema: s.ControlReplySchema
		}),
	/** Admin; der Status des core (202 oder 409) steht im Fehlertext, die Antwort ist sein Satz */
	acknowledge: (actuator: string): Promise<s.AckResponse> =>
		request('POST', `/api/control/locks/${encode(actuator)}/ack`, { schema: s.AckResponseSchema }),

	decisions: (query: s.DecisionQuery, o?: Load): Promise<s.DecisionsResponse> =>
		get(withQuery('/api/decisions', decisionParams(query)), s.DecisionsResponseSchema, o),
	latestDecisions: (o?: Load): Promise<s.LatestDecisionsResponse> =>
		get('/api/decisions/latest', s.LatestDecisionsResponseSchema, o),

	// Zeitplan des Akkus (Neuplanung und Netzladen: Operator)
	plan: (o?: Load): Promise<s.PlanResponse> => get('/api/plan', s.PlanResponseSchema, o),
	/** Plan gegen Ist für heute (der Tag ist der des Servers) */
	planReview: (o?: Load): Promise<s.PlanReview> => get('/api/plan/review', s.PlanReviewSchema, o),
	replan: async (): Promise<void> => {
		await request('POST', '/api/plan/replan'); // 202 {queued: true}
	},
	setGridCharge: (on: boolean): Promise<s.GridChargeReply> =>
		request('PUT', '/api/plan/grid-charge', { body: { on }, schema: s.GridChargeReplySchema })
};
