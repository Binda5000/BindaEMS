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
	departure: v.string(),
	/** Abfahrt an diesem Tag (`YYYY-MM-DD`, Plan 5b); `null` täglich; ältere Antworten ohne */
	departure_on: v.optional(v.nullable(v.string())),
	/** Höchststrom je Phase in „Sofort“ in A; `null` volle Leistung; ältere Antworten ohne */
	now_max_a: v.optional(v.nullable(v.number()))
});
export type VehicleIntent = v.InferOutput<typeof VehicleIntentSchema>;

/** Die Absicht (`Intent` des core) mit allen Feldern */
/** Akku-Unterstützung fürs Auto; `soc_pct` `null`: die Schwelle der Konfiguration */
export const SupportIntentSchema = v.object({
	allowed: v.boolean(),
	soc_pct: NullableNumber
});
export type SupportIntent = v.InferOutput<typeof SupportIntentSchema>;

/** Ausschnitt eines Slots des Plans in der Absicht; ältere Absichten haben keinen Planwert */
export const IntentPlanSlotSchema = v.object({
	start: Iso,
	thermal_steps: v.optional(NullableNumber, null)
});
export type IntentPlanSlot = v.InferOutput<typeof IntentPlanSlotSchema>;

export const IntentPlanSchema = v.object({
	/** bis hierhin führt der core den Plan aus */
	valid_until: Iso,
	slots: v.array(IntentPlanSlotSchema)
});
export type IntentPlan = v.InferOutput<typeof IntentPlanSchema>;

export const IntentSchema = v.object({
	version: v.number(),
	control: v.boolean(),
	actuators: v.record(v.string(), ActuatorModeSchema),
	vehicles: v.record(v.string(), VehicleIntentSchema),
	/** Reihenfolge der Fahrzeug-Schlüssel; `null`: die der Konfiguration */
	priority: v.nullable(v.array(v.string())),
	surplus_priority: SurplusPrioritySchema,
	guest: v.object({
		mode: ChargeModeSchema,
		/** Höchststrom je Phase in „Sofort“ in A; `null` volle Leistung; ältere Antworten ohne */
		now_max_a: v.optional(v.nullable(v.number()))
	}),
	support: SupportIntentSchema,
	/**
	 * Der Plan, den der core ausführt (Plan 3c), hier nur, was die Karte „Klimaanlage“ braucht:
	 * Gültigkeit und je Slot der Planwert in Stufen (Plan 5c); `null` ohne Plan.
	 */
	plan: v.optional(v.nullable(IntentPlanSchema))
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
		'charge_limit',
		'setpoint_offset'
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
	last_packet_at: v.optional(v.nullable(Iso), null),
	/** verworfene Pakete von einer fremden Absender-IP; ohne Adapter fehlt die Zahl */
	foreign_packets: v.optional(NullableNumber, null),
	/** verworfene Pakete, die sich nicht lesen ließen */
	bad_packets: v.optional(NullableNumber, null),
	/** Werte mit anderer Einheit als in der Empfangstabelle; sie gelten als ungültig */
	unit_mismatch: v.optional(NullableNumber, null),
	/** `aus`, `heizen`, `lueften`, `kuehlen` oder `unbekannt` */
	mode: v.optional(v.nullable(v.string()), null),
	running: v.optional(v.nullable(v.boolean()), null),
	actual_c: v.optional(NullableNumber, null),
	setpoint_c: v.optional(NullableNumber, null),
	/** Sollwert ohne den gesendeten Versatz */
	base_setpoint_c: v.optional(NullableNumber, null),
	offset_sent_k: v.optional(NullableNumber, null),
	/** Echo: der Versatz, den die UVR anzeigt */
	offset_active_k: v.optional(NullableNumber, null),
	power_w: v.optional(NullableNumber, null),
	/**
	 * Stufen des Wunsches, positiv heißt mehr Leistung (nicht der Versatz). Wunsch, Grund und
	 * Hinweis setzt die Regelung; solange sie ruht (Beobachten, Regelbetrieb aus, erster Zyklus),
	 * sendet der core `null`.
	 */
	wish: v.optional(NullableNumber, null),
	/** Code des Grundes der Strategie */
	reason: v.optional(v.nullable(v.string()), null),
	/** Hinweis des Plans im aktuellen Slot: `mehr`, `neutral` oder `weniger` */
	hint: v.optional(v.nullable(v.string()), null),
	/** Gelernte Leistung bei Versatz 0 in ganzen W; ein älterer core sendet das Feld nicht */
	base_w: v.optional(NullableNumber, null),
	/** Zuletzt freigebbare Leistung in ganzen W (`freeable_w`); ein älterer core sendet es nicht */
	spare_w: v.optional(NullableNumber, null),
	/**
	 * Planwert des laufenden Slots in Stufen (Plan 5c), positiv heißt mehr Leistung (nicht der
	 * Versatz); `null` ohne Planwert. Ein älterer core sendet das Feld nicht.
	 */
	planned: v.optional(NullableNumber, null)
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
			support_source: v.optional(v.nullable(SupportSourceSchema), null),
			/**
			 * Akku ins Auto: ob der Akku übrig ist (`on`, `off`, `plan`, `soc`) und wie viel er
			 * Geplant und Sofort geben darf; `null` vor der ersten Bewertung und bei älterem core
			 */
			spare: v.optional(v.nullable(v.picklist(['on', 'off', 'plan', 'soc'])), null),
			spare_w: v.optional(NullableNumber, null)
		}),
		{ support_soc_pct: null, support_source: null, spare: null, spare_w: null }
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
	/** Akku ins Auto, wenn übrig; ältere Einstellungen kennen den Schalter nicht */
	car_battery_allowed: v.optional(v.boolean(), false),
	margin_ct: v.number(),
	roundtrip_pct: v.number(),
	wear_ct: v.number(),
	end_value_factor: v.number(),
	inertia_ct: v.number()
});
export type PlanningSettings = v.InferOutput<typeof PlanningSettingsSchema>;

/** Abschnitt „Fahrverhalten“ (Plan 5b); die Schalter der Automatik setzt die Fahrzeugkarte */
export const DrivingSettingsSchema = v.looseObject({
	/** Puffer bei der Rückkehr in % (0–50) */
	return_reserve_pct: v.number(),
	/** Grenzen der vorgeschlagenen Ziel-SOC in % (20–100) */
	target_min_pct: v.number(),
	target_max_pct: v.number(),
	/** Automatik für Ziel und Abfahrt je Fahrzeug */
	automation: v.record(v.string(), v.boolean()),
	/** HA-Entität der Anwesenheit je Fahrzeug (z. B. `device_tracker.…`) */
	presence_entities: v.record(v.string(), v.string())
});
export type DrivingSettings = v.InferOutput<typeof DrivingSettingsSchema>;

/** Vorheizen und Vorkühlen: Schalter, Komfortband in K (Schritte von 0,5), Mindestvorteil in ct/kWh */
export const ThermalPlanSettingsSchema = v.looseObject({
	enabled: v.boolean(),
	heat_up_k: v.number(),
	heat_down_k: v.number(),
	cool_up_k: v.number(),
	cool_down_k: v.number(),
	margin_ct: v.number()
});
export type ThermalPlanSettings = v.InferOutput<typeof ThermalPlanSettingsSchema>;

export const LearningSettingsSchema = v.looseObject({
	/** Schalter „Lastmodell verwenden“ */
	load_model_enabled: v.boolean(),
	/** zusätzliche Sensoren aus Home Assistant, höchstens 8 */
	ha_sensors: v.array(v.string()),
	/** Fahrverhalten; ältere Stände haben den Abschnitt nicht (es gelten die Standardwerte) */
	driving: v.optional(DrivingSettingsSchema),
	/** Vorheizen und Vorkühlen (Plan 5c); ältere Stände haben den Abschnitt nicht */
	thermal: v.optional(ThermalPlanSettingsSchema)
});
export type LearningSettings = v.InferOutput<typeof LearningSettingsSchema>;

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
	learning: LearningSettingsSchema,
	/** eigene Namen der Ladestationen; ohne Eintrag gilt der Typ */
	wallbox_names: v.optional(v.record(v.string(), v.string()), {}),
	/** Modus, den die App nach dem Abstecken setzt; ohne Eintrag bleibt der Modus unverändert */
	wallbox_default_modes: v.optional(v.record(v.string(), ChargeModeSchema), {})
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

/** Verfahren, das die Lastprognose liefert: das Profil oder das gelernte Modell */
export const LoadMethodSchema = v.picklist(['profile', 'model']);
export type LoadMethod = v.InferOutput<typeof LoadMethodSchema>;

export const LoadForecastSchema = v.object({
	issued_at: v.nullable(Iso),
	slots: v.array(LoadForecastSlotSchema),
	days: v.array(v.object({ date: DateText, kwh: NullableNumber })),
	method: LoadMethodSchema,
	/** Viertelstunden des Profils: aus der Abrechnung (BindaEMS) und aus Home Assistant */
	basis: v.object({ ledger: v.number(), ha: v.number() }),
	status: ComponentStatusSchema
});
export type LoadForecast = v.InferOutput<typeof LoadForecastSchema>;

/** Art der Prognose: PV, die ausgegebene Last und die beiden Schatten (Profil, Modell) */
export const QualityKindSchema = v.picklist(['pv', 'load', 'load_profile', 'load_model']);
export type QualityKind = v.InferOutput<typeof QualityKindSchema>;

export const HorizonSchema = v.picklist(['h0_6', 'h6_24', 'h24']);
export type Horizon = v.InferOutput<typeof HorizonSchema>;

export const QualityRowSchema = v.object({
	kind: QualityKindSchema,
	horizon: HorizonSchema,
	/** bewertete Viertelstunden */
	n: v.number(),
	/** mittlerer absoluter Fehler in W */
	mae_w: v.number(),
	/** mittlerer Fehler (Prognose − Ist) in W */
	bias_w: v.number(),
	/** mittlerer absoluter Fehler im Verhältnis zur Ist-Leistung; ohne Ist-Leistung null */
	rel_mae_pct: NullableNumber,
	/** nur bei `load_model`: wie viel Prozent genauer als `load_profile` derselben Vorlaufklasse */
	improvement_pct: NullableNumber
});
export type QualityRow = v.InferOutput<typeof QualityRowSchema>;

export const QualitySchema = v.object({ days: v.number(), rows: v.array(QualityRowSchema) });
export type Quality = v.InferOutput<typeof QualitySchema>;

export const QualityDailyRowSchema = v.object({
	date: DateText,
	kind: QualityKindSchema,
	/** bewertete Viertelstunden des Tages */
	n: v.number(),
	/** mittlerer absoluter Fehler des Tages in W, alle Vorlaufklassen zusammen */
	mae_w: v.number()
});
export type QualityDailyRow = v.InferOutput<typeof QualityDailyRowSchema>;

/** `GET /api/forecast/quality/daily`: nach Datum und Art sortiert */
export const QualityDailySchema = v.object({
	days: v.number(),
	rows: v.array(QualityDailyRowSchema)
});
export type QualityDaily = v.InferOutput<typeof QualityDailySchema>;

// --- Lastmodell ----------------------------------------------------------------------------

export const ActivationSchema = v.object({
	/** das Modell liefert die Lastprognose der Planung */
	active: v.boolean(),
	basis: v.picklist(['shadow', 'backtest', 'none']),
	/** Tage, auf denen der Vergleich beruht */
	days: v.number(),
	model_mae_w: NullableNumber,
	profile_mae_w: NullableNumber,
	improvement_pct: NullableNumber,
	reason: v.picklist(['better', 'not_better', 'too_few_days', 'disabled', 'no_model']),
	/** der Grund als deutscher Satz */
	text: v.string(),
	decided_at: v.nullable(Iso)
});
export type Activation = v.InferOutput<typeof ActivationSchema>;

export const ModelFeatureSchema = v.object({
	name: v.string(),
	/** Anzeigename; bei einem HA-Sensor die Entität */
	label: v.string(),
	/** Anteil am Gewinn der Splits, Summe 1 */
	importance: v.number()
});
export type ModelFeature = v.InferOutput<typeof ModelFeatureSchema>;

export const LoadModelSchema = v.object({
	id: v.number(),
	trained_at: Iso,
	data_from: Iso,
	data_to: Iso,
	/** Viertelstunden im Training */
	samples: v.number(),
	/** nach Anteil absteigend */
	features: v.array(ModelFeatureSchema)
});
export type LoadModel = v.InferOutput<typeof LoadModelSchema>;

export const BacktestWeekSchema = v.object({
	origin: Iso,
	end: Iso,
	horizon: HorizonSchema,
	n: v.number(),
	model_mae_w: v.number(),
	profile_mae_w: v.number(),
	improvement_pct: NullableNumber
});
export type BacktestWeek = v.InferOutput<typeof BacktestWeekSchema>;

export const BacktestSchema = v.object({
	weeks: v.array(BacktestWeekSchema),
	total: v.object({
		model_mae_w: v.number(),
		profile_mae_w: v.number(),
		improvement_pct: NullableNumber
	})
});
export type Backtest = v.InferOutput<typeof BacktestSchema>;

export const TrainingSchema = v.object({
	running: v.boolean(),
	last_at: v.nullable(Iso),
	/** null: noch kein Lauf seit dem Start */
	outcome: v.nullable(v.picklist(['trained', 'too_little_data', 'failed'])),
	text: v.nullable(v.string())
});
export type Training = v.InferOutput<typeof TrainingSchema>;

/** `GET /api/learning/load` */
export const LearningStatusSchema = v.object({
	/** Schalter „Lastmodell verwenden“ */
	enabled: v.boolean(),
	method: LoadMethodSchema,
	activation: ActivationSchema,
	model: v.nullable(LoadModelSchema),
	backtest: v.nullable(BacktestSchema),
	training: TrainingSchema,
	/** die gewählten HA-Sensoren; `in_model`: das Modell nutzt sie (Anteil am Gewinn über 0) */
	sensors: v.array(v.object({ entity_id: v.string(), in_model: v.boolean() })),
	status: v.object({ ok: v.boolean(), text: v.string() })
});
export type LearningStatus = v.InferOutput<typeof LearningStatusSchema>;

/** `GET /api/learning/candidates` (Admin): Entitäten der HA-Datenbank zur Auswahl */
export const SensorCandidatesSchema = v.object({
	entities: v.array(v.object({ entity_id: v.string(), unit: v.nullable(v.string()) })),
	/** Grund, warum die Liste fehlt */
	error: v.nullable(v.string())
});
export type SensorCandidates = v.InferOutput<typeof SensorCandidatesSchema>;

// --- Fahrverhalten (Plan 5b) ---------------------------------------------------------------

export const ConfidenceSchema = v.picklist(['high', 'medium', 'low']);
export type Confidence = v.InferOutput<typeof ConfidenceSchema>;

/** Herkunft der Anwesenheit: HA-Entität, `home_entity` der Konfiguration, Tessie oder Ansteckvorgänge */
export const DrivingSourceSchema = v.picklist(['ha', 'home_entity', 'tessie', 'sessions']);
export type DrivingSource = v.InferOutput<typeof DrivingSourceSchema>;

/** Ein Wochentag (0 = Montag … 6 = Sonntag und Feiertag); Uhrzeiten `HH:MM`, fehlende Werte `null` */
export const DrivingWeekdaySchema = v.object({
	weekday: v.number(),
	/** `group`: zu wenige Tage, das Profil rechnet mit der Gruppe (Mo–Fr, Sa, So/Feiertag) */
	basis: v.picklist(['weekday', 'group']),
	days: v.number(),
	trips: v.number(),
	trip_pct: v.number(),
	/** Vorschlag Abfahrt (auf die Viertelstunde abgerundetes P20) */
	departure: v.nullable(v.string()),
	departure_p50: v.nullable(v.string()),
	departure_p80: v.nullable(v.string()),
	departure_confidence: v.nullable(ConfidenceSchema),
	arrival_p50: v.nullable(v.string()),
	arrival_soc_p50: NullableNumber,
	consumption_p50_pp: NullableNumber,
	consumption_p80_pp: NullableNumber,
	/** Vorschlag Ziel-SOC; `target_clamped`: auf die Grenzen der Einstellungen begrenzt */
	target_soc_pct: NullableNumber,
	target_clamped: v.boolean(),
	target_confidence: v.nullable(ConfidenceSchema)
});
export type DrivingWeekday = v.InferOutput<typeof DrivingWeekdaySchema>;

export const DrivingTripSchema = v.object({
	date: DateText,
	departure: v.string(),
	departure_confidence: ConfidenceSchema,
	target_soc_pct: NullableNumber,
	target_confidence: v.nullable(ConfidenceSchema)
});
export type DrivingTrip = v.InferOutput<typeof DrivingTripSchema>;

/** Zustand der Automatik für Ziel und Abfahrt eines Autos; `text` ist der fertige Satz */
export const AutomationStateSchema = v.object({
	enabled: v.boolean(),
	state: v.picklist([
		'off',
		'pending',
		'inactive',
		'waiting',
		'paused',
		'uncertain',
		'applied',
		'error'
	]),
	text: v.string(),
	paused_until: v.nullable(Iso),
	last_set_at: v.nullable(Iso)
});
export type AutomationState = v.InferOutput<typeof AutomationStateSchema>;

/** Ein Auto ohne abgelegten Stand: `source`, `data_from`, `data_to` sind `null`, `days` ist 0 */
export const DrivingVehicleSchema = v.object({
	key: v.string(),
	name: v.string(),
	source: v.nullable(DrivingSourceSchema),
	data_from: v.nullable(Iso),
	data_to: v.nullable(Iso),
	days: v.number(),
	weekdays: v.array(DrivingWeekdaySchema),
	next_trip: v.nullable(DrivingTripSchema),
	/** Wahrscheinlichkeit angesteckt je Viertelstunde der nächsten 48 Stunden; leer ohne Stand */
	presence: v.array(v.object({ start: Iso, p: v.number() })),
	/** Trefferquote der letzten 28 Tage; `null` ohne bewertete Tage */
	quality: v.object({
		days: v.number(),
		departure_n: v.number(),
		departure_hit_pct: NullableNumber,
		target_n: v.number(),
		target_hit_pct: NullableNumber
	}),
	automation: AutomationStateSchema
});
export type DrivingVehicle = v.InferOutput<typeof DrivingVehicleSchema>;

/** `GET /api/learning/driving` */
export const DrivingStatusSchema = v.object({
	computed_at: v.nullable(Iso),
	running: v.boolean(),
	status: v.object({ ok: v.boolean(), text: v.string() }),
	vehicles: v.array(DrivingVehicleSchema)
});
export type DrivingStatus = v.InferOutput<typeof DrivingStatusSchema>;

// --- Wärmemodell der Klimaanlage (Plan 5c) -------------------------------------------------

export const ThermalModeSchema = v.picklist(['heizen', 'kuehlen']);
export type ThermalMode = v.InferOutput<typeof ThermalModeSchema>;

/** Warum das Modell einer Betriebsart (noch) nicht taugt; `ok` taugt */
export const ThermalModelReasonSchema = v.picklist([
	'ok',
	'few_days',
	'few_episodes',
	'poor_fit',
	'implausible_tau',
	'no_data'
]);
export type ThermalModelReason = v.InferOutput<typeof ThermalModelReasonSchema>;

/** Das Modell einer Betriebsart; Zahlen, die sich nicht bestimmen lassen, sind `null` */
export const ThermalModelSchema = v.object({
	mode: ThermalModeSchema,
	days: v.number(),
	/** Grundleistung: W je K Temperaturdifferenz, W je kW PV und konstanter Anteil in W */
	u_w_per_k: NullableNumber,
	pv_w_per_kw: NullableNumber,
	base_w: NullableNumber,
	r2: NullableNumber,
	/** Zeitkonstante in h und Auskühl-Phasen, aus denen sie stammt */
	tau_h: NullableNumber,
	episodes: v.number(),
	/** Speicher in kWh Strom je K; `c_model_kwh_per_k` der Wert aus Grundleistung und Zeitkonstante */
	c_kwh_per_k: NullableNumber,
	c_model_kwh_per_k: NullableNumber,
	/** Versatz-Ereignisse im Betrieb, aus denen der Speicher nachgeführt wird, und ihr Fehler in % */
	events: v.number(),
	event_error_pct: NullableNumber,
	max_w: NullableNumber,
	usable: v.boolean(),
	reason: ThermalModelReasonSchema,
	gap_w_per_k: NullableNumber
});
export type ThermalModel = v.InferOutput<typeof ThermalModelSchema>;

/** Eine Betriebsart ohne Stand hat `model` und `computed_at` `null` (Grund `no_data`); `text` ist der
 * fertige Satz zum Zustand */
export const ThermalModeStatusSchema = v.object({
	mode: ThermalModeSchema,
	model: v.nullable(ThermalModelSchema),
	usable: v.boolean(),
	reason: ThermalModelReasonSchema,
	text: v.string(),
	computed_at: v.nullable(Iso)
});
export type ThermalModeStatus = v.InferOutput<typeof ThermalModeStatusSchema>;

/** `GET /api/learning/thermal` (404 ohne Klimaanlage); `enabled` ist der Schalter „Vorheizen und
 * Vorkühlen“ */
export const ThermalStatusSchema = v.object({
	enabled: v.boolean(),
	computed_at: v.nullable(Iso),
	running: v.boolean(),
	status: v.object({ ok: v.boolean(), text: v.string() }),
	modes: v.array(ThermalModeStatusSchema)
});
export type ThermalStatus = v.InferOutput<typeof ThermalStatusSchema>;

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
	/** Woche/Monat: je Tag, Jahr: je Monat (gleiche `start` wie `buckets`); Tag: leer */
	vehicle_buckets: v.array(
		v.object({
			start: v.string(),
			/** Reihenfolge wie `vehicles`, nur mit Energie */
			vehicles: v.array(
				v.object({
					key: v.string(),
					kwh: v.number(),
					pv_kwh: v.number(),
					battery_kwh: v.number(),
					grid_kwh: v.number(),
					cost_eur: NullableNumber
				})
			)
		})
	),
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
	'ZIEL_SOC_UNERREICHBAR',
	'AUTO_ERWARTET',
	'KLIMA_MEHR',
	'KLIMA_WENIGER',
	'AUTO_AUS_AKKU',
	'SOFORT_AUS_AKKU'
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
	'vehicles',
	'thermal'
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
	/**
	 * Akku ins Auto: der Teil von `ev_w`, der aus dem Akku kommt (W je Fahrzeug, kann leer sein),
	 * und ob Autos in Sofort in diesem Slot aus dem Akku laden dürfen. Ältere Pläne haben die
	 * Felder nicht.
	 */
	ev_battery_w: v.optional(v.record(v.string(), v.number()), {}),
	now_battery: v.optional(v.boolean(), false),
	codes: v.array(PlanReasonCodeSchema),
	text: v.string(),
	/**
	 * Vorheizen und Vorkühlen (Plan 5c): Mehr- oder Minderverbrauch der Klimaanlage in W, die
	 * Stufen (positiv mehr Leistung; `null` ohne Verschiebung im Plan) und der Netzbezug dafür in W.
	 * Ältere Pläne haben die Felder nicht.
	 */
	thermal_w: v.optional(v.number(), 0),
	thermal_steps: v.optional(NullableNumber, null),
	thermal_grid_w: v.optional(v.number(), 0)
});
export type PlanSlot = v.InferOutput<typeof PlanSlotSchema>;

/** Betriebsart der Klimaanlage beim Planen (aus Health) */
export const PlanThermalModeSchema = v.picklist([
	'heizen',
	'kuehlen',
	'lueften',
	'aus',
	'unbekannt'
]);
export type PlanThermalMode = v.InferOutput<typeof PlanThermalModeSchema>;

/**
 * Vorheizen und Vorkühlen im Plan (Plan 5c): `active` heißt, die Verschiebung geht an den core;
 * mit `reason` `ok` und `active` falsch ist sie Vorschau. `saving_ct` ist die Ersparnis in ct.
 */
export const PlanThermalSchema = v.object({
	device: v.string(),
	mode: PlanThermalModeSchema,
	active: v.boolean(),
	saving_ct: v.number(),
	model_computed_at: v.nullable(Iso),
	reason: v.picklist(['ok', 'off', 'no_model', 'mode', 'not_worth', 'solver'])
});
export type PlanThermal = v.InferOutput<typeof PlanThermalSchema>;

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
	reachable: v.boolean(),
	/** erwartetes Auto (Plan 5b): noch nicht angesteckt, die Anwesenheitsprognose erwartet es ab `arrival_at` */
	expected: v.boolean(),
	arrival_at: v.nullable(Iso)
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
	/** `null` ohne Klimaanlage; ältere Pläne haben das Feld nicht */
	thermal: v.optional(v.nullable(PlanThermalSchema), null),
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
