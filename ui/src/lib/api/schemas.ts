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

/** Betriebsart eines Aktors (nicht der Betriebszustand des core) */
export const ActuatorModeSchema = v.picklist(['dry_run', 'live']);
export type ActuatorMode = v.InferOutput<typeof ActuatorModeSchema>;
const Mode = ActuatorModeSchema;

// --- Absicht der Bedienung ---------------------------------------------------------------

/** Lademodus, den die Bedienung einstellt (der geltende Modus „boost“ steht nur in Health) */
export const ChargeModeSchema = v.picklist(['pv', 'min_pv', 'now', 'off', 'planned']);
export type ChargeMode = v.InferOutput<typeof ChargeModeSchema>;

/** Geltender Modus eines Fahrzeugs in Health: der eingestellte, bei aktivem Boost `boost` */
export const EffectiveModeSchema = v.picklist(['pv', 'min_pv', 'now', 'off', 'planned', 'boost']);
export type EffectiveMode = v.InferOutput<typeof EffectiveModeSchema>;

export const SurplusPrioritySchema = v.object({
	kind: v.picklist(['cars_first', 'battery_first', 'battery_until_soc']),
	soc_pct: v.number()
});
export type SurplusPriority = v.InferOutput<typeof SurplusPrioritySchema>;

export const VehicleIntentSchema = v.object({
	mode: ChargeModeSchema,
	boost_until: v.nullable(Iso),
	wallbox: v.nullable(v.string()),
	/** Ziel-SOC für „Geplant“ in %, ganze Zahl 20 bis 100 */
	target_soc_pct: v.number(),
	/** Tägliche Abfahrt für „Geplant“, lokale Uhrzeit `HH:MM` */
	departure: v.string()
});
export type VehicleIntent = v.InferOutput<typeof VehicleIntentSchema>;

/** Die Absicht (`Intent` des core) mit allen Feldern */
/** Akku-Unterstützung fürs Auto; `soc_pct` `null`: die Schwelle der Konfiguration */
export const SupportIntentSchema = v.object({
	allowed: v.boolean(),
	soc_pct: NullableNumber
});
export type SupportIntent = v.InferOutput<typeof SupportIntentSchema>;

export const IntentSchema = v.object({
	version: v.number(),
	control: v.boolean(),
	actuators: v.record(v.string(), ActuatorModeSchema),
	vehicles: v.record(v.string(), VehicleIntentSchema),
	/** Reihenfolge der Fahrzeug-Schlüssel; `null`: die der Konfiguration */
	priority: v.nullable(v.array(v.string())),
	surplus_priority: SurplusPrioritySchema,
	guest: v.object({ mode: ChargeModeSchema }),
	support: SupportIntentSchema
});
export type Intent = v.InferOutput<typeof IntentSchema>;

/** `GET /api/control`: `intent` ist `null`, solange der core fehlt oder keine Absicht hat */
export const ControlResponseSchema = v.object({
	core_connected: v.boolean(),
	intent: v.nullable(IntentSchema)
});
export type ControlResponse = v.InferOutput<typeof ControlResponseSchema>;

/** Antwort jeder Änderung: `clamped` zeigt in `details`, was der core begrenzt hat */
export const ControlReplySchema = v.object({
	result: v.picklist(['accepted', 'clamped']),
	version: v.number(),
	details: v.array(v.string()),
	intent: IntentSchema
});
export type ControlReply = v.InferOutput<typeof ControlReplySchema>;

/**
 * `GET /api/control/boost-cost`: Mehrkosten des Boosts je Fahrzeug (Leistungspreis im Monat).
 * `vehicles` ist leer, solange `applies` falsch ist (kein Leistungspreis mit Satz im Monat).
 */
export const BoostCostSchema = v.object({
	applies: v.boolean(),
	month_peak_kw: NullableNumber,
	vehicles: v.array(v.object({ id: v.string(), extra_kw: v.number(), cost_eur: v.number() }))
});
export type BoostCost = v.InferOutput<typeof BoostCostSchema>;

/** Antwort der Quittierung: der Satz des core */
export const AckResponseSchema = v.object({ detail: v.string() });
export type AckResponse = v.InferOutput<typeof AckResponseSchema>;

// --- Entscheidungen ----------------------------------------------------------------------

export const DecisionResultSchema = v.picklist([
	'ok',
	'error',
	'timeout',
	'not_sent',
	'dry_run',
	'rejected',
	'deferred',
	'foreign'
]);
export type DecisionResult = v.InferOutput<typeof DecisionResultSchema>;

export const DecisionSchema = v.object({
	/** eindeutig (Wanduhr des core) */
	id: v.string(),
	ts: Iso,
	actuator: v.string(),
	kind: v.picklist([
		'current',
		'charging',
		'grid_setpoint',
		'max_discharge',
		'min_soc',
		'charge_limit'
	]),
	requested: v.nullable(v.union([v.number(), v.boolean()])),
	applied: v.nullable(v.union([v.number(), v.boolean()])),
	dry_run: v.boolean(),
	outcome: v.string(),
	result: DecisionResultSchema,
	codes: v.array(v.string()),
	text: v.string(),
	inputs: v.record(v.string(), NullableNumber),
	/** Stelle in der Tabelle der app, zum Blättern (`before`); der Stream des core hat keine */
	cursor: v.optional(v.number())
});
export type Decision = v.InferOutput<typeof DecisionSchema>;

export const DecisionsResponseSchema = v.object({
	decisions: v.array(DecisionSchema),
	more: v.boolean()
});
export type DecisionsResponse = v.InferOutput<typeof DecisionsResponseSchema>;

export const LatestDecisionsResponseSchema = v.object({ decisions: v.array(DecisionSchema) });
export type LatestDecisionsResponse = v.InferOutput<typeof LatestDecisionsResponseSchema>;

/** Filter und Seite von `GET /api/decisions` */
export interface DecisionQuery {
	since?: string;
	until?: string;
	actuator?: string;
	result?: DecisionResult;
	/** `false` blendet Dry-Run-Entscheidungen aus (Standard des Servers: an) */
	dry_run?: boolean;
	text?: string;
	/** `cursor` der letzten Zeile der Seite */
	before?: number;
	limit?: number;
}

// --- Gesundheitsbericht des core ---------------------------------------------------------

const AssignmentKindSchema = v.picklist(['detected', 'default', 'override', 'unclear']);
export type AssignmentKind = v.InferOutput<typeof AssignmentKindSchema>;

export const ClueSchema = v.picklist([
	'tessie',
	'current',
	'timing',
	'phases',
	'home',
	'elsewhere'
]);
export type Clue = v.InferOutput<typeof ClueSchema>;

export const SupportSourceSchema = v.picklist(['config', 'intent', 'plan']);
export type SupportSource = v.InferOutput<typeof SupportSourceSchema>;

/** Quelle des Ladestands eines Fahrzeugs: gemessen, aus dem Zähler geschätzt oder angenommen */
export const SocSourceSchema = v.picklist(['measured', 'estimated', 'assumed']);
export type SocSource = v.InferOutput<typeof SocSourceSchema>;

/**
 * „Geplant“ im Health (Plan 4b): `state` `plan` (ein gültiger Slot nennt das Auto), `surplus`
 * (PV-Überschuss), `due` (ab der spätesten Startzeit volle Leistung) oder `reached` (am Ziel);
 * `target_soc_pct` ist das wirksame Ziel, `latest_start` `null` ohne Bedarf
 */
export const PlannedHealthSchema = v.object({
	state: v.picklist(['plan', 'surplus', 'due', 'reached']),
	target_soc_pct: v.number(),
	departure_at: Iso,
	need_kwh: v.number(),
	latest_start: v.nullable(Iso),
	plan_w: NullableNumber
});
export type PlannedHealth = v.InferOutput<typeof PlannedHealthSchema>;

export const VehicleHealthSchema = v.object({
	id: v.string(),
	name: v.string(),
	/** `null`: der Stand der Regelung ist unbekannt (Regelbetrieb aus) */
	mode: v.nullable(EffectiveModeSchema),
	boost_until: v.nullable(Iso),
	wallbox: v.nullable(v.string()),
	assignment: v.nullable(AssignmentKindSchema),
	limit_a: NullableNumber,
	plugged: v.nullable(v.boolean()),
	charging: v.nullable(v.boolean()),
	wakes_today: v.number(),
	/** Befehle der letzten Stunde; nur Fahrzeuge mit Tessie */
	commands_hour: NullableNumber,
	/** Boost bzw. Übersteuerung gelten, warten aber auf den nächsten Ansteckvorgang */
	boost_pending: v.boolean(),
	override_pending: v.boolean(),
	/** letzter Grund des letzten Befehls (Code) */
	reason: v.nullable(v.string()),
	/** Ladestand in % und woher er kommt (Plan 4b); `assumed` heißt angenommen mit 0 % */
	soc_pct: v.optional(NullableNumber, null),
	soc_source: v.optional(v.nullable(SocSourceSchema), null),
	/** Ladelimit im Auto in %; nur mit Tessie */
	charge_limit_pct: v.optional(NullableNumber, null),
	/** Stand von „Geplant“ an einer Wallbox; sonst `null` */
	planned: v.optional(v.nullable(PlannedHealthSchema), null)
});
export type VehicleHealth = v.InferOutput<typeof VehicleHealthSchema>;

export const WallboxHealthSchema = v.object({
	id: v.string(),
	/** Schlüssel des Fahrzeugs, `guest` für einen Gast, `null` ohne Vorgang */
	occupant: v.nullable(v.string()),
	name: v.nullable(v.string()),
	assignment: v.nullable(AssignmentKindSchema),
	indicators: v.array(v.object({ vehicle: v.string(), clue: ClueSchema, supports: v.boolean() }))
});
export type WallboxHealth = v.InferOutput<typeof WallboxHealthSchema>;

/** Eine Klimaanlage im Gesundheitsbericht (CoE, Plan 6a); Zahlen sind `null`, solange sie fehlen */
export const ThermalHealthSchema = v.object({
	label: v.string(),
	/** frische Werte der Anlage liegen vor */
	connected: v.boolean(),
	last_packet_at: v.nullable(Iso),
	/** verworfene Pakete von einer fremden Absender-IP */
	foreign_packets: v.number(),
	/** verworfene Pakete, die sich nicht lesen ließen */
	bad_packets: v.number(),
	/** `aus`, `heizen`, `lueften`, `kuehlen` oder `unbekannt` */
	mode: v.string(),
	running: v.nullable(v.boolean()),
	actual_c: NullableNumber,
	setpoint_c: NullableNumber,
	/** Sollwert ohne den gesendeten Versatz */
	base_setpoint_c: NullableNumber,
	offset_sent_k: NullableNumber,
	/** Echo: der Versatz, den die UVR anzeigt */
	offset_active_k: NullableNumber,
	/** Stufen des Wunsches, positiv heißt mehr Leistung (nicht der Versatz) */
	wish: v.number(),
	power_w: NullableNumber,
	/** Code des Grundes der Strategie */
	reason: v.nullable(v.string()),
	/** Hinweis des Plans im aktuellen Slot: `mehr`, `neutral` oder `weniger` */
	hint: v.string()
});
export type ThermalHealth = v.InferOutput<typeof ThermalHealthSchema>;

export const CoreHealthSchema = v.object({
	mode: v.picklist(['START', 'OBSERVE', 'CONTROL', 'DEGRADED', 'SAFE']),
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
	cycle_ms_p95: NullableNumber,
	/** seit wann der Betriebszustand gilt */
	mode_since: v.nullable(Iso),
	/** warum der Betriebszustand gilt (z. B. warum eingeschränkt) */
	mode_reasons: v.array(v.string()),
	/** die gültige Absicht der Bedienung; `null`: noch keine */
	intent: v.nullable(
		v.object({ version: v.number(), control: v.boolean(), actuators: v.record(v.string(), Mode) })
	),
	/** Version der Absicht, die die Regelung zuletzt übernommen hat; `null`: keine */
	intent_version: v.nullable(v.number()),
	actuators: v.array(
		v.object({
			id: v.string(),
			label: v.string(),
			requested: Mode,
			effective: Mode,
			live_allowed: v.boolean(),
			/** warum ein angeforderter Live-Betrieb nicht gilt */
			reasons: v.array(v.string()),
			/** warum der Aktor gesperrt ist (z. B. wegen eines mitregelnden Systems) */
			locked: v.array(v.string()),
			/** der Betrieb im Regelbetrieb braucht den Watchdog (Victron, EVCS) */
			watchdog_needed: v.boolean()
		})
	),
	vehicles: v.array(VehicleHealthSchema),
	wallboxes: v.array(WallboxHealthSchema),
	/** Klimaanlagen nach Name; fehlt der Block (älterer core), gibt es keine */
	thermal: v.optional(v.record(v.string(), ThermalHealthSchema), {}),
	/**
	 * Akku-Sollwert: die wirksame Schwelle der Unterstützung und woher sie kommt. Fehlt der Block
	 * oder ein Feld (älterer core), ist die Schwelle unbekannt; Health bleibt gültig.
	 */
	battery: v.optional(
		v.object({
			/** `null` vor dem ersten Zyklus */
			support_soc_pct: v.optional(NullableNumber, null),
			support_source: v.optional(v.nullable(SupportSourceSchema), null)
		}),
		{ support_soc_pct: null, support_source: null }
	),
	watchdog: v.object({
		state: v.picklist(['waiting', 'armed', 'tripped', 'disabled', 'unknown']),
		trip_count: NullableNumber,
		last_trip: v.nullable(Iso),
		last_trip_failures: NullableNumber,
		version: v.nullable(v.string()),
		expected_version: v.string(),
		heartbeat: v.picklist(['off', 'active', 'released']),
		/** der Betrieb braucht ihn: Victron oder EVCS ist im Regelbetrieb live gewünscht oder wird übergeben */
		required: v.boolean(),
		/** der Watchdog ist für diesen core scharf */
		armed: v.boolean()
	}),
	inputs: v.object({ ok: v.boolean(), problems: v.array(v.string()) })
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

const ComponentBase = {
	id: v.string(),
	name: v.string(),
	valid_from: v.nullable(DateText),
	valid_until: v.nullable(DateText)
};

/** Arbeitspreis des Bezugs (ct/kWh netto); ohne `kind` gespeicherte Daten liefert der Server als dieses */
export const EnergyComponentSchema = v.looseObject({
	...ComponentBase,
	kind: v.literal('energy_per_kwh'),
	source: v.picklist(['fixed', 'spot']),
	value_ct: NullableNumber,
	vat: v.boolean(),
	windows: v.array(TimeWindowSchema)
});
export type EnergyComponent = v.InferOutput<typeof EnergyComponentSchema>;

/** Zu- oder Abschlag auf die Einspeisung (ct/kWh netto, auch negativ) */
export const FeedInComponentSchema = v.looseObject({
	...ComponentBase,
	kind: v.literal('feed_in_per_kwh'),
	value_ct: NullableNumber,
	vat: v.boolean(),
	windows: v.array(TimeWindowSchema)
});
export type FeedInComponent = v.InferOutput<typeof FeedInComponentSchema>;

/** Leistungspreis (€/kW/Jahr netto), gestuft nach `tier_kw` */
export const PowerComponentSchema = v.looseObject({
	...ComponentBase,
	kind: v.literal('power_per_kw_year'),
	value_eur: NullableNumber,
	value_above_eur: NullableNumber,
	tier_kw: v.number(),
	minimum_kw: v.number(),
	vat: v.boolean()
});
export type PowerComponent = v.InferOutput<typeof PowerComponentSchema>;

/** Monatliche Gebühr (€/Monat netto) */
export const MonthlyFeeComponentSchema = v.looseObject({
	...ComponentBase,
	kind: v.literal('fixed_per_month'),
	value_eur: NullableNumber,
	vat: v.boolean()
});
export type MonthlyFeeComponent = v.InferOutput<typeof MonthlyFeeComponentSchema>;

export const PriceComponentSchema = v.variant('kind', [
	EnergyComponentSchema,
	FeedInComponentSchema,
	PowerComponentSchema,
	MonthlyFeeComponentSchema
]);
export type PriceComponent = v.InferOutput<typeof PriceComponentSchema>;

export const PriceSettingsSchema = v.looseObject({
	vat_mode: v.picklist(['auto', 'net', 'gross']),
	vat_fallback: v.picklist(['net', 'gross']),
	reference_source: v.picklist(['energy_charts', 'awattar'])
});
export type PriceSettings = v.InferOutput<typeof PriceSettingsSchema>;

export const PlanningSettingsSchema = v.looseObject({
	grid_charge_allowed: v.boolean(),
	margin_ct: v.number(),
	roundtrip_pct: v.number(),
	wear_ct: v.number(),
	end_value_factor: v.number(),
	inertia_ct: v.number()
});
export type PlanningSettings = v.InferOutput<typeof PlanningSettingsSchema>;

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
	planning: PlanningSettingsSchema,
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
	/** Konfigurierte Schwelle der Akku-Unterstützung in % (`control.support_soc_pct`) */
	control: v.object({ support_soc_pct: v.number() }),
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

export const ForecastSlotSchema = v.object({
	start: Iso,
	p50_w: v.number(),
	p25_w: v.number(),
	p75_w: v.number()
});
export type ForecastSlot = v.InferOutput<typeof ForecastSlotSchema>;

export const ForecastSchema = v.object({
	issued_at: v.nullable(Iso),
	source: v.nullable(v.picklist(['open_meteo'])),
	slots: v.array(ForecastSlotSchema),
	days: v.array(v.object({ date: DateText, kwh: NullableNumber })),
	status: ComponentStatusSchema
});
export type Forecast = v.InferOutput<typeof ForecastSchema>;

export const LoadForecastSlotSchema = v.object({ start: Iso, p50_w: v.number() });
export type LoadForecastSlot = v.InferOutput<typeof LoadForecastSlotSchema>;

export const LoadForecastSchema = v.object({
	issued_at: v.nullable(Iso),
	slots: v.array(LoadForecastSlotSchema),
	days: v.array(v.object({ date: DateText, kwh: NullableNumber })),
	/** Viertelstunden des Profils: aus der Abrechnung (BindaEMS) und aus Home Assistant */
	basis: v.object({ ledger: v.number(), ha: v.number() }),
	status: ComponentStatusSchema
});
export type LoadForecast = v.InferOutput<typeof LoadForecastSchema>;

export const QualityRowSchema = v.object({
	kind: v.picklist(['pv', 'load']),
	horizon: v.picklist(['h0_6', 'h6_24', 'h24']),
	/** bewertete Viertelstunden */
	n: v.number(),
	/** mittlerer absoluter Fehler in W */
	mae_w: v.number(),
	/** mittlerer Fehler (Prognose − Ist) in W */
	bias_w: v.number(),
	/** mittlerer absoluter Fehler im Verhältnis zur Ist-Leistung; ohne Ist-Leistung null */
	rel_mae_pct: NullableNumber
});
export type QualityRow = v.InferOutput<typeof QualityRowSchema>;

export const QualitySchema = v.object({ days: v.number(), rows: v.array(QualityRowSchema) });
export type Quality = v.InferOutput<typeof QualitySchema>;

export const DaySummarySchema = v.object({
	date: DateText,
	slots: v.number(),
	/** vergangene Viertelstunden (am laufenden Tag bis jetzt) */
	expected_slots: v.number(),
	/** davon aus Zählerständen nachgerechnet */
	estimated_slots: v.number(),
	/** der Tag ist vorbei */
	complete: v.boolean(),
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

// --- Statistik, Ladesessions, Abgleich (Plan 4a) -------------------------------------------

export const StatsPeriodSchema = v.picklist(['day', 'week', 'month', 'year']);
export type StatsPeriod = v.InferOutput<typeof StatsPeriodSchema>;

/** Eine Zeile des Abgleichs über einen Zeitraum: Summe der Abrechnung gegen die der Zähler */
export const CheckTotalSchema = v.object({
	/** `import`, `export`, `pv` oder `wb:<id>` */
	signal: v.string(),
	ledger_kwh: v.number(),
	counter_kwh: NullableNumber,
	deviation_pct: NullableNumber
});
export type CheckTotal = v.InferOutput<typeof CheckTotalSchema>;

export const StatsSchema = v.object({
	period: StatsPeriodSchema,
	first: DateText,
	last: DateText,
	/** alle Tage des Zeitraums sind vorbei */
	complete: v.boolean(),
	coverage: v.number(),
	/** Viertelstunden, die aus Zählerständen nachgerechnet wurden */
	estimated_slots: v.number(),
	energy: v.object({
		pv_kwh: v.number(),
		import_kwh: v.number(),
		export_kwh: v.number(),
		house_kwh: v.number(),
		wallbox_kwh: v.number(),
		battery_charge_kwh: v.number(),
		battery_discharge_kwh: v.number(),
		consumption_kwh: v.number()
	}),
	money: v.object({
		import_eur: NullableNumber,
		revenue_eur: NullableNumber,
		net_eur: NullableNumber,
		fees_eur: v.number(),
		/** nur Monat und Jahr und nur mit Leistungspreis */
		power_eur: NullableNumber,
		total_eur: NullableNumber,
		savings_no_plant_eur: NullableNumber,
		savings_same_import_eur: NullableNumber
	}),
	ratios: v.object({ autarky: NullableNumber, self_consumption: NullableNumber }),
	/** Tag: Stunden (`start` mit Zone); Woche und Monat: Tage; Jahr: Monate (`JJJJ-MM`) */
	buckets: v.array(
		v.object({
			start: v.string(),
			pv_kwh: v.number(),
			import_kwh: v.number(),
			export_kwh: v.number(),
			consumption_kwh: v.number(),
			net_eur: NullableNumber
		})
	),
	/** in der Reihenfolge der Konfiguration, dann `guest` und `unknown`; nur mit Energie */
	vehicles: v.array(
		v.object({
			key: v.string(),
			name: v.string(),
			kwh: v.number(),
			pv_kwh: v.number(),
			battery_kwh: v.number(),
			grid_kwh: v.number(),
			cost_eur: NullableNumber,
			sessions: v.number()
		})
	),
	battery_to_vehicles_kwh: v.number(),
	/**
	 * Geplante Ladungen mit Abfahrt im Zeitraum (Abnahme „Ziel-SOC“): `reached` von `feasible`
	 * machbaren erreicht, `missed` verfehlt; `infeasible` nicht machbar, `early` vor der Abfahrt
	 * abgesteckt, `unknown` ohne Ladestand, `changed` vor der Abfahrt geändert oder verlassen;
	 * offene zählen nicht
	 */
	planned: v.object({
		reached: v.number(),
		feasible: v.number(),
		missed: v.number(),
		infeasible: v.number(),
		early: v.number(),
		unknown: v.number(),
		changed: v.number()
	}),
	consumers: v.array(
		v.object({
			id: v.number(),
			name: v.string(),
			kwh: NullableNumber,
			/** Grund, wenn `kwh` fehlt */
			note: v.nullable(v.string())
		})
	),
	month_peaks: v.array(
		v.object({
			month: v.string(),
			kw: v.number(),
			slot_start: Iso,
			power_eur: NullableNumber
		})
	),
	checks: v.object({
		days: v.number(),
		flagged_days: v.array(DateText),
		totals: v.array(CheckTotalSchema)
	})
});
export type Stats = v.InferOutput<typeof StatsSchema>;

export const ChargingSessionSchema = v.object({
	id: v.number(),
	wallbox: v.string(),
	wallbox_name: v.string(),
	/** Schlüssel des Autos, `guest` oder `unknown` */
	vehicle: v.string(),
	vehicle_name: v.string(),
	start: Iso,
	/** `null`, solange die Session läuft */
	end: v.nullable(Iso),
	open: v.boolean(),
	kwh: v.number(),
	pv_kwh: v.number(),
	battery_kwh: v.number(),
	grid_kwh: v.number(),
	cost_eur: NullableNumber
});
export type ChargingSession = v.InferOutput<typeof ChargingSessionSchema>;

export const DayCheckSchema = v.object({
	day: DateText,
	signal: v.string(),
	ledger_kwh: v.number(),
	counter_kwh: NullableNumber,
	deviation_pct: NullableNumber,
	flagged: v.boolean(),
	/** Grund, wenn der Tag nicht abgeglichen werden konnte */
	note: v.nullable(v.string())
});
export type DayCheck = v.InferOutput<typeof DayCheckSchema>;

export const LedgerChecksSchema = v.object({
	days: v.array(DayCheckSchema),
	totals: v.array(CheckTotalSchema)
});
export type LedgerChecks = v.InferOutput<typeof LedgerChecksSchema>;

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

// --- Zeitplan des Akkus (Plan 3b) --------------------------------------------------------

export const BatteryIntentKindSchema = v.picklist(['EIGENVERBRAUCH', 'HALTEN', 'NETZLADEN']);
export type BatteryIntentKind = v.InferOutput<typeof BatteryIntentKindSchema>;

export const PlanReasonCodeSchema = v.picklist([
	'NETZLADEN_GUENSTIG',
	'HALTEN_FUER_SPITZE',
	'BUDGET_SPITZE',
	'HALTEN_RESTWERT',
	'PV_LADEN',
	'PV_UEBERSCHUSS_ERWARTET',
	'REGEL_R_BLOCKIERT_NETZLADEN',
	'NETZLADEN_AUS',
	'EIGENVERBRAUCH',
	'UNTERSTUETZUNG_RESERVIERT',
	'GESCHAETZT',
	'ERSATZPLAN',
	'ZIEL_SOC_ABFAHRT',
	'AUTO_WARTET_AUF_PV',
	'ZIEL_SOC_UNERREICHBAR'
]);
export type PlanReasonCode = v.InferOutput<typeof PlanReasonCodeSchema>;

export const PlanTriggerSchema = v.picklist([
	'start',
	'slot',
	'prices',
	'pv_forecast',
	'load_forecast',
	'soc',
	'month_peak',
	'settings',
	'manual',
	'vehicles'
]);
export type PlanTrigger = v.InferOutput<typeof PlanTriggerSchema>;

/** Eine Viertelstunde des Plans; Leistungen sind Mittelwerte in W, `text` der fertige Satz */
export const PlanSlotSchema = v.object({
	start: Iso,
	intent: BatteryIntentKindSchema,
	grid_charge_w: v.number(),
	soc_pct: v.number(),
	pv_w: v.number(),
	load_w: v.number(),
	import_w: v.number(),
	export_w: v.number(),
	/** Netto-Akkuleistung: Laden minus Entladen */
	battery_w: v.number(),
	import_ct: v.number(),
	feed_in_ct: NullableNumber,
	estimated: v.boolean(),
	budget_kw: NullableNumber,
	/** Plan-Schwelle der Akku-Unterstützung in % */
	support_soc_pct: NullableNumber,
	/** geplante Ladeleistung je Fahrzeug in W (Schlüssel aus `vehicles`; nur Autos mit Leistung) */
	ev_w: v.record(v.string(), v.number()),
	codes: v.array(PlanReasonCodeSchema),
	text: v.string()
});
export type PlanSlot = v.InferOutput<typeof PlanSlotSchema>;

/** Zusammenfassung des Plans für ein geplantes Auto (Plan 4b) */
export const PlanVehicleSchema = v.object({
	key: v.string(),
	name: v.string(),
	wallbox: v.string(),
	/** wirksames Ziel in % (unter dem Ladelimit des Autos, wenn es nicht angeglichen wird) */
	target_soc_pct: v.number(),
	departure_at: Iso,
	/** Ladestand zu Beginn in %; `soc_assumed`: nicht gemessen, nicht geschätzt */
	soc_start_pct: v.number(),
	soc_assumed: v.boolean(),
	/** Ladelimit im Auto in %, `null` unbekannt */
	limit_pct: NullableNumber,
	need_kwh: v.number(),
	planned_kwh: v.number(),
	shortfall_kwh: v.number(),
	/** Ladestand an der Abfahrt, wenn der Plan so läuft */
	expected_soc_pct: v.number(),
	/** Ende des letzten Slots mit Laden, `null` ohne Laden */
	finish_at: v.nullable(Iso),
	reachable: v.boolean()
});
export type PlanVehicle = v.InferOutput<typeof PlanVehicleSchema>;

export const PlanSchema = v.object({
	id: v.nullable(v.number()),
	created_at: Iso,
	trigger: PlanTriggerSchema,
	source: v.picklist(['optimizer', 'fallback']),
	solver_status: v.nullable(
		v.picklist(['optimal', 'time_limit', 'infeasible', 'timeout', 'error'])
	),
	fallback_reason: v.nullable(v.string()),
	valid_until: Iso,
	/** gilt der Plan jetzt noch? */
	valid: v.boolean(),
	horizon_end: Iso,
	reserve_pct: v.number(),
	soc_max_pct: v.number(),
	month_peak_kw: NullableNumber,
	/** gilt im Horizont ein Leistungspreis? */
	power_price: v.boolean(),
	expected_cost_eur: v.number(),
	baseline_cost_eur: v.number(),
	issues: v.array(v.string()),
	vehicles: v.array(PlanVehicleSchema),
	slots: v.array(PlanSlotSchema)
});
export type Plan = v.InferOutput<typeof PlanSchema>;

/** Stand der Ausführung des Plans im core (Plan 3c); `live` ist der einzige, der den Plan ausführt */
export const ExecutionStateSchema = v.picklist([
	'unreachable',
	'pending',
	'off',
	'idle',
	'locked',
	'dry_run',
	'live'
]);
export type ExecutionState = v.InferOutput<typeof ExecutionStateSchema>;

/** Antwort von `GET /api/plan`: Status der Komponente, Schalter, der letzte Plan (oder keiner) und,
 * solange er gilt, der Stand seiner Ausführung mit dem fertigen Satz */
export const PlanResponseSchema = v.object({
	status: v.object({ ok: v.boolean(), message: v.string(), running: v.boolean() }),
	grid_charge_allowed: v.boolean(),
	plan: v.nullable(PlanSchema),
	execution: v.nullable(v.object({ state: ExecutionStateSchema, text: v.string() }))
});
export type PlanResponse = v.InferOutput<typeof PlanResponseSchema>;

// --- Plan gegen Ist (Plan 3c) ------------------------------------------------------------

/** Der Slot des damals gültigen Plans; Leistungen sind Mittelwerte der Viertelstunde in W */
export const ReviewPlannedSchema = v.object({
	intent: BatteryIntentKindSchema,
	grid_charge_w: v.number(),
	/** geplanter SOC am Ende der Viertelstunde */
	soc_end_pct: v.number(),
	import_w: v.number(),
	export_w: v.number(),
	battery_w: v.number(),
	import_cost_ct: v.number(),
	plan_id: v.nullable(v.number()),
	source: v.picklist(['optimizer', 'fallback'])
});
export type ReviewPlanned = v.InferOutput<typeof ReviewPlannedSchema>;

/** Die Abrechnung der Viertelstunde; ohne Bezugspreis sind die Kosten `null` */
export const ReviewActualSchema = v.object({
	import_w: v.number(),
	export_w: v.number(),
	battery_w: v.number(),
	grid_charge_w: v.number(),
	import_cost_ct: NullableNumber
});
export type ReviewActual = v.InferOutput<typeof ReviewActualSchema>;

/** Summen über die Viertelstunden mit Plan und Ist; Kosten `null` ohne bekannten Bezugspreis */
export const ReviewTotalsSchema = v.object({
	import_kwh: v.number(),
	export_kwh: v.number(),
	grid_charge_kwh: v.number(),
	import_cost_eur: NullableNumber
});
export type ReviewTotals = v.InferOutput<typeof ReviewTotalsSchema>;

/** Antwort von `GET /api/plan/review`: die vergangenen Viertelstunden des Tages */
export const PlanReviewSchema = v.object({
	day: DateText,
	slots: v.array(
		v.object({
			start: Iso,
			planned: v.nullable(ReviewPlannedSchema),
			actual: v.nullable(ReviewActualSchema)
		})
	),
	totals: v.object({ planned: ReviewTotalsSchema, actual: ReviewTotalsSchema })
});
export type PlanReview = v.InferOutput<typeof PlanReviewSchema>;

/** Antwort von `PUT /api/plan/grid-charge` */
export const GridChargeReplySchema = v.object({
	grid_charge_allowed: v.boolean(),
	settings_version: v.number()
});
export type GridChargeReply = v.InferOutput<typeof GridChargeReplySchema>;
