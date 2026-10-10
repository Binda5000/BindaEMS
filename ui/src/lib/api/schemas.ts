// Antwortformen der API (Plan 1c, Anhang A) als Schemas: jede Antwort wird zur Laufzeit geprüft.
// `v.object` übergeht unbekannte Felder (vorwärtskompatibel). Die Einstellungen sind „loose“:
// sie gehen beim Speichern vollständig zurück an den Server, unbekannte Felder bleiben erhalten.
import * as v from 'valibot';

const Iso = v.string(); // Zeitstempel mit Versatz, oft mit Mikrosekunden
const DateText = v.string(); // YYYY-MM-DD
const NullableNumber = v.nullable(v.number());
const Details = v.record(v.string(), v.unknown());
const Source = v.picklist(['ui', 'ha', 'cli', 'system']);
const Phase = v.picklist(['L1', 'L2', 'L3']);
const PhaseValues = v.object({ L1: NullableNumber, L2: NullableNumber, L3: NullableNumber });

// --- Anmeldung und Benutzer --------------------------------------------------------------

export const RoleSchema = v.picklist(['admin', 'operator', 'viewer']);
export type Role = v.InferOutput<typeof RoleSchema>;

export const UserSchema = v.object({
	id: v.number(),
	username: v.string(),
	role: RoleSchema,
	totp_enabled: v.boolean(),
	created_at: Iso
});
export type User = v.InferOutput<typeof UserSchema>;

export const MeResponseSchema = v.object({ user: UserSchema });
export type MeResponse = v.InferOutput<typeof MeResponseSchema>;

export const TotpSetupSchema = v.object({ secret: v.string(), uri: v.string() });
export type TotpSetup = v.InferOutput<typeof TotpSetupSchema>;

export const AuditEntrySchema = v.object({
	id: v.number(),
	ts: Iso,
	actor: v.string(),
	source: Source,
	action: v.string(),
	target: v.nullable(v.string()),
	details: v.nullable(Details)
});
export type AuditEntry = v.InferOutput<typeof AuditEntrySchema>;

// --- Live und System ---------------------------------------------------------------------

export const ComponentStatusSchema = v.object({
	name: v.string(),
	ok: v.boolean(),
	message: v.string(),
	since: v.nullable(Iso),
	details: Details
});
export type ComponentStatus = v.InferOutput<typeof ComponentStatusSchema>;

export const AlarmSchema = v.object({
	id: v.string(),
	severity: v.picklist(['info', 'warning', 'error']),
	message: v.string(),
	since: Iso
});
export type Alarm = v.InferOutput<typeof AlarmSchema>;

export const ReadingSchema = v.object({
	v: v.nullable(v.union([v.number(), v.string(), v.boolean()])),
	ts: Iso,
	q: v.picklist(['ok', 'stale', 'invalid'])
});
export type Reading = v.InferOutput<typeof ReadingSchema>;

export const DerivedSchema = v.object({
	grid_w: NullableNumber, // + = Bezug
	pv_total_w: NullableNumber,
	consumption_w: NullableNumber,
	battery_ac_w: NullableNumber,
	battery_w: NullableNumber, // + = Laden
	house_load_w: NullableNumber,
	consumption_l: PhaseValues,
	wallbox_w: v.record(v.string(), NullableNumber),
	wallbox_phase_a: v.record(
		v.string(),
		v.object({ L1: v.number(), L2: v.number(), L3: v.number() })
	),
	phase_import_a: PhaseValues,
	balance_residual_w: NullableNumber
});
export type Derived = v.InferOutput<typeof DerivedSchema>;

export const CoreStateSchema = v.object({
	ts: Iso,
	signals: v.record(v.string(), ReadingSchema),
	derived: DerivedSchema
});
export type CoreState = v.InferOutput<typeof CoreStateSchema>;

export const CoreHealthSchema = v.object({
	mode: v.picklist(['OBSERVE']),
	version: v.string(),
	adapters: v.array(
		v.object({
			name: v.string(),
			connected: v.boolean(),
			last_ok: v.nullable(Iso),
			last_error: v.nullable(v.string()),
			error_count: v.number()
		})
	),
	selfcheck: v.array(
		v.object({
			id: v.string(),
			status: v.picklist(['ok', 'warn', 'fail', 'unknown']),
			message: v.string()
		})
	),
	alarms: v.array(AlarmSchema),
	cycle_ms_p95: NullableNumber
});
export type CoreHealth = v.InferOutput<typeof CoreHealthSchema>;

export const StateResponseSchema = v.object({
	core_connected: v.boolean(),
	updated_at: v.nullable(Iso),
	state: v.nullable(CoreStateSchema),
	alarms: v.array(AlarmSchema)
});
export type StateResponse = v.InferOutput<typeof StateResponseSchema>;

export const SystemResponseSchema = v.object({
	core: v.object({ connected: v.boolean(), health: v.nullable(CoreHealthSchema) }),
	components: v.array(ComponentStatusSchema),
	warnings: v.array(v.string())
});
export type SystemResponse = v.InferOutput<typeof SystemResponseSchema>;

export const LiveMessageSchema = v.variant('type', [
	v.object({
		type: v.literal('hello'),
		data: v.object({
			core_connected: v.boolean(),
			state: v.nullable(CoreStateSchema),
			alarms: v.array(AlarmSchema)
		})
	}),
	v.object({ type: v.literal('state'), data: CoreStateSchema }),
	v.object({ type: v.literal('alarm'), data: v.array(AlarmSchema) }),
	v.object({ type: v.literal('core'), data: v.object({ connected: v.boolean() }) })
]);
export type LiveMessage = v.InferOutput<typeof LiveMessageSchema>;

export const HealthSchema = v.object({ status: v.picklist(['ok']), version: v.string() });
export type Health = v.InferOutput<typeof HealthSchema>;

// --- Einstellungen -----------------------------------------------------------------------

export const TimeWindowSchema = v.looseObject({
	months: v.array(v.number()),
	weekdays: v.array(v.number()), // 0 = Montag
	start: v.string(), // HH:MM:SS
	end: v.string(),
	factor: NullableNumber,
	value_ct: NullableNumber
});
export type TimeWindow = v.InferOutput<typeof TimeWindowSchema>;

export const PriceComponentSchema = v.looseObject({
	id: v.string(),
	name: v.string(),
	kind: v.picklist(['energy_per_kwh']),
	source: v.picklist(['fixed', 'spot']),
	value_ct: NullableNumber,
	vat: v.boolean(),
	valid_from: v.nullable(DateText),
	valid_until: v.nullable(DateText),
	windows: v.array(TimeWindowSchema)
});
export type PriceComponent = v.InferOutput<typeof PriceComponentSchema>;

export const PriceSettingsSchema = v.looseObject({
	vat_mode: v.picklist(['auto', 'net', 'gross']),
	vat_fallback: v.picklist(['net', 'gross']),
	reference_source: v.picklist(['energy_charts', 'awattar'])
});
export type PriceSettings = v.InferOutput<typeof PriceSettingsSchema>;

export const RuntimeSettingsSchema = v.looseObject({
	prices: PriceSettingsSchema,
	tariff: v.looseObject({
		vat_pct: v.number(),
		components: v.array(PriceComponentSchema),
		fixed_price_gross_ct: v.number()
	}),
	feed_in: v.looseObject({ monthly_ct: v.record(v.string(), v.number()) }),
	pv_model: v.looseObject({
		performance_ratio: v.number(),
		temp_coeff_pct_per_k: v.number(),
		noct_c: v.number()
	}),
	/** eigene Namen der Ladestationen; ohne Eintrag gilt der Typ */
	wallbox_names: v.optional(v.record(v.string(), v.string()), {})
});
export type RuntimeSettings = v.InferOutput<typeof RuntimeSettingsSchema>;

export const SettingsMetaSchema = v.object({
	version: v.number(),
	created_at: Iso,
	actor: v.string(),
	source: Source,
	comment: v.nullable(v.string())
});
export type SettingsMeta = v.InferOutput<typeof SettingsMetaSchema>;

export const SettingsCurrentSchema = v.object({
	...SettingsMetaSchema.entries,
	settings: RuntimeSettingsSchema,
	warnings: v.array(v.string())
});
export type SettingsCurrent = v.InferOutput<typeof SettingsCurrentSchema>;

const EvcsLimitsSchema = v.object({
	type: v.literal('victron_evcs_ns'),
	name: v.nullable(v.string()),
	min_a: v.number(),
	max_a: v.number(),
	safe_a: v.number(),
	phase_map: v.array(Phase),
	live_allowed: v.boolean()
});
const TwcLimitsSchema = v.object({
	type: v.literal('tesla_wall_connector_gen3'),
	name: v.nullable(v.string()),
	max_a: v.number(),
	phase_map: v.array(Phase)
});

export const LimitsSchema = v.object({
	grid: v.object({
		phases: v.literal(3),
		voltage_nominal_v: v.number(),
		fuse_a: v.number(),
		fuse_margin_a: v.number()
	}),
	battery: v.object({
		usable_kwh: v.number(),
		reserve_soc_pct: v.number(),
		max_charge_w: v.number(),
		max_discharge_w: v.number(),
		soc_max_pct: v.number()
	}),
	victron: v.object({
		max_grid_charge_setpoint_w: v.number(),
		persistent_writes: v.object({ per_hour: v.number(), per_day: v.number() }),
		watchdog: v.object({ heartbeat_s: v.number(), timeout_s: v.number() })
	}),
	wallboxes: v.record(v.string(), v.variant('type', [EvcsLimitsSchema, TwcLimitsSchema])),
	vehicles: v.record(
		v.string(),
		v.object({
			name: v.string(),
			usable_kwh: v.number(),
			phases: v.picklist([1, 2, 3]),
			min_a: v.number(),
			max_a: v.number(),
			default_wallbox: v.string(),
			live_allowed: v.boolean()
		})
	)
});
export type Limits = v.InferOutput<typeof LimitsSchema>;

// --- Verbraucher -------------------------------------------------------------------------

export const ConsumerSchema = v.object({
	id: v.number(),
	name: v.string(),
	group: v.nullable(v.string()),
	parent_id: v.nullable(v.number()),
	color: v.string(),
	source_kind: v.picklist(['core', 'ha']),
	power_ref: v.string(),
	power_unit: v.nullable(v.picklist(['W', 'kW'])),
	sort: v.number()
});
export type Consumer = v.InferOutput<typeof ConsumerSchema>;

/** Knoten im Verbraucherbaum; „Sonstiges“ ist `other_w` eines Knotens mit Kindern. */
export interface TreeNode {
	id: number | null;
	name: string;
	color: string | null;
	power_w: number | null;
	/** warum `power_w` fehlt, z. B. „kein Wert in den letzten 24 h“ */
	note: string | null;
	other_w: number | null;
	mismatch: boolean;
	children: TreeNode[];
	/** Energie seit Mitternacht */
	energy_kwh: number | null;
	/** warum `energy_kwh` fehlt */
	energy_note: string | null;
	other_kwh: number | null;
}

export const TreeNodeSchema: v.GenericSchema<TreeNode> = v.object({
	id: v.nullable(v.number()),
	name: v.string(),
	color: v.nullable(v.string()),
	power_w: NullableNumber,
	note: v.nullable(v.string()),
	other_w: NullableNumber,
	mismatch: v.boolean(),
	children: v.array(v.lazy(() => TreeNodeSchema)),
	energy_kwh: NullableNumber,
	energy_note: v.nullable(v.string()),
	other_kwh: NullableNumber
});

export const ConsumersResponseSchema = v.object({
	tree: TreeNodeSchema,
	consumers: v.array(ConsumerSchema),
	/** Beginn des Energiezeitraums (Mitternacht); `null`: noch nicht berechnet */
	energy_since: v.nullable(Iso)
});
export type ConsumersResponse = v.InferOutput<typeof ConsumersResponseSchema>;

export const CandidatesSchema = v.object({
	core: v.array(v.object({ ref: v.string() })),
	ha: v.array(v.object({ entity_id: v.string(), unit: v.picklist(['W', 'kW']) })),
	/** warum HA-Vorschläge fehlen (Datenbank nicht eingerichtet oder nicht lesbar) */
	ha_error: v.nullable(v.string())
});
export type Candidates = v.InferOutput<typeof CandidatesSchema>;

// --- Verlauf -----------------------------------------------------------------------------

export const CatalogEntrySchema = v.object({ id: v.string(), label: v.string(), unit: v.string() });
export type CatalogEntry = v.InferOutput<typeof CatalogEntrySchema>;

export const HistoryResponseSchema = v.object({
	rp: v.picklist(['raw', 'long']),
	step_s: v.number(),
	series: v.record(
		v.string(),
		v.object({
			label: v.string(),
			unit: v.string(),
			points: v.array(v.tuple([v.number(), v.number()])) // [Epoch-ms, Mittelwert]
		})
	)
});
export type HistoryResponse = v.InferOutput<typeof HistoryResponseSchema>;

// --- Preise, Prognose, Abrechnung --------------------------------------------------------

export const PriceSlotSchema = v.object({
	start: Iso,
	spot_net_ct: v.number(),
	import_net_ct: v.number(),
	import_gross_ct: v.number(),
	feed_in_ct: NullableNumber,
	origin: v.picklist(['primary', 'fallback']),
	missing: v.array(v.string())
});
export type PriceSlot = v.InferOutput<typeof PriceSlotSchema>;

export const PriceStatusSchema = v.object({
	last_attempt: v.nullable(Iso),
	last_success: v.nullable(Iso),
	vat_mode: v.nullable(v.picklist(['net', 'gross'])),
	vat_detection: v.nullable(
		v.object({
			result: v.nullable(v.picklist(['net', 'gross'])),
			ratio: NullableNumber,
			slots: v.number()
		})
	),
	days: v.array(
		v.object({
			date: DateText,
			origin: v.nullable(v.picklist(['primary', 'fallback'])),
			findings: v.array(v.string())
		})
	),
	errors: v.array(v.string())
});
export type PriceStatus = v.InferOutput<typeof PriceStatusSchema>;

export const PricesResponseSchema = v.object({
	status: PriceStatusSchema,
	slots: v.array(PriceSlotSchema)
});
export type PricesResponse = v.InferOutput<typeof PricesResponseSchema>;

export const PricesNowSchema = v.object({
	now: v.nullable(PriceSlotSchema),
	next_3h: v.array(PriceSlotSchema)
});
export type PricesNow = v.InferOutput<typeof PricesNowSchema>;

export const ForecastSlotSchema = v.object({ start: Iso, p50_w: v.number() });
export type ForecastSlot = v.InferOutput<typeof ForecastSlotSchema>;

export const ForecastSchema = v.object({
	issued_at: v.nullable(Iso),
	source: v.nullable(v.picklist(['open_meteo'])),
	slots: v.array(ForecastSlotSchema),
	days: v.array(v.object({ date: DateText, kwh: NullableNumber })),
	status: ComponentStatusSchema
});
export type Forecast = v.InferOutput<typeof ForecastSchema>;

export const DaySummarySchema = v.object({
	date: DateText,
	slots: v.number(),
	expected_slots: v.number(),
	coverage: v.number(),
	pv_kwh: v.number(),
	import_kwh: v.number(),
	export_kwh: v.number(),
	house_kwh: v.number(),
	wallbox_kwh: v.record(v.string(), v.number()),
	battery_charge_kwh: v.number(),
	battery_discharge_kwh: v.number(),
	consumption_kwh: v.number(),
	counter_kwh: v.record(v.string(), v.number()),
	cost_eur: NullableNumber,
	revenue_eur: NullableNumber,
	net_cost_eur: NullableNumber,
	autarky: NullableNumber,
	self_consumption: NullableNumber,
	savings_no_plant_eur: NullableNumber,
	savings_same_import_eur: NullableNumber
});
export type DaySummary = v.InferOutput<typeof DaySummarySchema>;

// --- Fehler ------------------------------------------------------------------------------

export const ValidationErrorSchema = v.object({
	detail: v.array(
		v.object({
			type: v.string(),
			loc: v.array(v.union([v.string(), v.number()])),
			msg: v.string(),
			input: v.optional(v.unknown()),
			ctx: v.optional(Details)
		})
	)
});
export type ValidationError = v.InferOutput<typeof ValidationErrorSchema>;

// --- Anfragekörper -----------------------------------------------------------------------

export interface LoginBody {
	username: string;
	password: string;
	totp?: string | null;
	remember?: boolean;
}

export interface NewUser {
	username: string;
	password: string;
	role: Role;
}

export interface UserPatch {
	role?: Role;
	password?: string;
}

export interface ConsumerInput {
	name: string;
	group?: string | null;
	parent_id?: number | null;
	color: string;
	source_kind: 'core' | 'ha';
	power_ref: string;
	power_unit?: 'W' | 'kW' | null;
	sort?: number;
}
