// Einstellungen im UI: Entwurf als Text (deutsche Dezimalzahlen), Prüfung wie der Server,
// Texte für Zeitfenster und Gültigkeit, Zuordnung von Serverfehlern zu den Feldern
import type {
	EnergyComponent,
	FeedInComponent,
	MonthlyFeeComponent,
	PowerComponent,
	PriceComponent,
	LearningSettings,
	PlanningSettings,
	PriceSettings,
	RuntimeSettings,
	TimeWindow
} from '$lib/api/schemas';
import { fieldErrors } from '$lib/api/errors';
import { todayVienna } from '$lib/time';
import { WALLBOX_NAME_MAX } from '$lib/wallboxes';

export type ComponentKind =
	'energy_per_kwh' | 'power_per_kw_year' | 'fixed_per_month' | 'feed_in_per_kwh';

/** Zeitfenster als Text: Uhrzeiten „HH:MM“, `amount` ist Faktor oder fester Wert je nach `mode` */
export interface WindowDraft {
	months: number[]; // 1–12
	weekdays: number[]; // 0 = Montag
	start: string;
	end: string;
	mode: 'factor' | 'value';
	amount: string;
}

/**
 * Bestandteil als Text. `value` ist ct/kWh (Energie, Einspeisung) oder €/kW/Jahr bzw. €/Monat
 * (Leistungspreis, Gebühr); `source`, `valueAbove`, `tierKw`, `minimumKw` und `windows` gelten nur
 * für die Arten, zu denen sie gehören.
 */
export interface ComponentDraft {
	id: string;
	kind: ComponentKind;
	name: string;
	source: 'fixed' | 'spot';
	value: string;
	valueAbove: string;
	tierKw: string;
	minimumKw: string;
	vat: boolean;
	validFrom: string; // JJJJ-MM-TT oder leer
	validUntil: string;
	windows: WindowDraft[];
}

export interface TariffDraft {
	vatPct: string;
	fixedPriceCt: string;
	components: ComponentDraft[];
}

/** Planung des Akkus als Text (Zahlen mit Dezimalkomma) */
export interface PlanningDraft {
	gridChargeAllowed: boolean;
	marginCt: string;
	roundtripPct: string;
	wearCt: string;
	endValueFactor: string;
	inertiaCt: string;
}

/** Lastmodell: Schalter und die gewählten HA-Sensoren (Entitäts-IDs) */
export interface LearningDraft {
	loadModelEnabled: boolean;
	haSensors: string[];
}

/** So viele zusätzliche Sensoren erlaubt `LearningSettings.ha_sensors` höchstens */
export const MAX_HA_SENSORS = 8;

export interface SettingsDraft {
	tariff: TariffDraft;
	feedIn: { month: string; ct: string }[];
	prices: PriceSettings;
	planning: PlanningDraft;
	learning: LearningDraft;
	/** eigene Namen der Ladestationen nach Schlüssel; leer = Typname */
	wallboxNames: Record<string, string>;
	comment: string;
}

export type DraftResult =
	{ settings: RuntimeSettings; tariffChanged: boolean } | { errors: Record<string, string> };

/** Bezeichnung der Arten in Leseansicht und Auswahl des Editors */
export const KIND_LABEL: Record<ComponentKind, string> = {
	energy_per_kwh: 'Energie (ct/kWh)',
	power_per_kw_year: 'Leistungspreis (€/kW/Jahr)',
	fixed_per_month: 'Monatliche Gebühr (€/Monat)',
	feed_in_per_kwh: 'Einspeisung (ct/kWh)'
};

/** Vorbelegter Name eines neuen Bestandteils je Art */
export const NEW_COMPONENT_NAME: Record<ComponentKind, string> = {
	energy_per_kwh: 'Neue Energie',
	power_per_kw_year: 'Neuer Leistungspreis',
	fixed_per_month: 'Neue Gebühr',
	feed_in_per_kwh: 'Neue Einspeisung'
};

const COMPONENT_KINDS: string[] = [
	'energy_per_kwh',
	'power_per_kw_year',
	'fixed_per_month',
	'feed_in_per_kwh'
];
const DECIMAL_RE = /^-?\d+(?:[.,]\d+)?$/;
const MONTH_RE = /^\d{4}-(0[1-9]|1[0-2])$/;
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const TIME_RE = /^(\d{2}):(\d{2})(?::00)?$/;
const NAME_MAX = 80;
const ID_MAX = 40;
const QUARTER_ERROR = 'Uhrzeit auf einer Viertelstunde, z. B. 10:15';
const NUMBER_ERROR = 'Zahl, z. B. 8,11';
const NON_NEGATIVE_ERROR = 'Zahl ab 0, z. B. 2,0';
const PERCENT_ERROR = 'Zahl von 50 bis 100';
const FACTOR_ERROR = 'Zahl von 0 bis 1, z. B. 0,9';
const DEFAULT_TIER_KW = '10';
const DEFAULT_MINIMUM_KW = '2';
export const MONTHS = [
	'Jän',
	'Feb',
	'Mär',
	'Apr',
	'Mai',
	'Jun',
	'Jul',
	'Aug',
	'Sep',
	'Okt',
	'Nov',
	'Dez'
];
export const WEEKDAYS = ['Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa', 'So']; // 0 = Montag

/** Dezimalzahl mit Komma oder Punkt; leer = kein Wert */
export function parseDecimal(text: string): number | null | 'invalid' {
	const trimmed = text.trim();
	if (trimmed === '') return null;
	if (!DECIMAL_RE.test(trimmed)) return 'invalid';
	const value = Number(trimmed.replace(',', '.'));
	return Number.isFinite(value) ? value : 'invalid';
}

export function decimalText(value: number | null): string {
	return value === null ? '' : String(value).replace('.', ',');
}

/** Energiebestandteil (ct/kWh); nur diese haben `source` und Zeitfenster im Editor */
export function isEnergy(component: PriceComponent): component is EnergyComponent {
	return component.kind === 'energy_per_kwh';
}

/** Zeitfenster des Bestandteils; Leistungspreis und Gebühr haben keine */
export function windowsOf(component: PriceComponent): TimeWindow[] {
	return component.kind === 'energy_per_kwh' || component.kind === 'feed_in_per_kwh'
		? component.windows
		: [];
}

function windowDraft(window: TimeWindow): WindowDraft {
	const byValue = window.value_ct !== null;
	return {
		months: [...window.months],
		weekdays: [...window.weekdays],
		start: window.start.slice(0, 5),
		end: window.end.slice(0, 5),
		mode: byValue ? 'value' : 'factor',
		amount: decimalText(byValue ? window.value_ct : window.factor)
	};
}

/** Neues Zeitfenster: ganzjährig, alle Tage, ganzer Tag, Faktor 1 */
export function newWindow(): WindowDraft {
	return {
		months: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12],
		weekdays: [0, 1, 2, 3, 4, 5, 6],
		start: '00:00',
		end: '00:00',
		mode: 'factor',
		amount: '1'
	};
}

function componentDraft(component: PriceComponent): ComponentDraft {
	const draft: ComponentDraft = {
		id: component.id,
		kind: component.kind,
		name: component.name,
		source: 'fixed',
		value: '',
		valueAbove: '',
		tierKw: DEFAULT_TIER_KW,
		minimumKw: DEFAULT_MINIMUM_KW,
		vat: component.vat,
		validFrom: component.valid_from ?? '',
		validUntil: component.valid_until ?? '',
		windows: windowsOf(component).map(windowDraft)
	};
	switch (component.kind) {
		case 'energy_per_kwh':
			draft.source = component.source;
			draft.value = decimalText(component.value_ct);
			break;
		case 'feed_in_per_kwh':
			draft.value = decimalText(component.value_ct);
			break;
		case 'power_per_kw_year':
			draft.value = decimalText(component.value_eur);
			draft.valueAbove = decimalText(component.value_above_eur);
			draft.tierKw = decimalText(component.tier_kw);
			draft.minimumKw = decimalText(component.minimum_kw);
			break;
		case 'fixed_per_month':
			draft.value = decimalText(component.value_eur);
			break;
	}
	return draft;
}

/** ID aus dem Namen: Kleinbuchstaben, Ziffern und `_`, höchstens 40 Zeichen, unter den vorhandenen eindeutig */
export function componentId(name: string, existingIds: string[]): string {
	const base =
		name
			.toLowerCase()
			.replace(/ä/g, 'ae')
			.replace(/ö/g, 'oe')
			.replace(/ü/g, 'ue')
			.replace(/ß/g, 'ss')
			.replace(/[^a-z0-9]+/g, '_')
			.replace(/^_+|_+$/g, '')
			.slice(0, ID_MAX)
			.replace(/_+$/g, '') || 'bestandteil';
	if (!existingIds.includes(base)) return base;
	for (let n = 2; ; n++) {
		const suffix = `_${n}`;
		const id = base.slice(0, ID_MAX - suffix.length).replace(/_+$/g, '') + suffix;
		if (!existingIds.includes(id)) return id;
	}
}

/** Name und Beginn des ElWG-Leistungspreises, wenn noch keiner angelegt ist (Einstellungen vor 3a) */
const ELWG_POWER_NAME = 'Leistungspreis (ElWG)';
const ELWG_POWER_VALID_FROM = '2027-01-01';

/**
 * Neuer, leerer Bestandteil; die ID folgt aus dem Namen und bleibt danach fest. Gibt es noch
 * keinen Leistungspreis (`existingKinds`), ist der erste die ElWG-Vorlage: Name und Beginn
 * 1. 1. 2027 sind vorbelegt, Stufe und Mindestverrechnung gelten ohnehin.
 */
export function newComponent(
	kind: ComponentKind,
	name: string,
	existingIds: string[],
	existingKinds: ComponentKind[]
): ComponentDraft {
	const elwg = kind === 'power_per_kw_year' && !existingKinds.includes('power_per_kw_year');
	if (elwg) name = ELWG_POWER_NAME;
	return {
		id: componentId(name, existingIds),
		kind,
		name,
		source: 'fixed',
		value: '',
		valueAbove: '',
		tierKw: DEFAULT_TIER_KW,
		minimumKw: DEFAULT_MINIMUM_KW,
		vat: kind !== 'feed_in_per_kwh', // Einspeisung ohne USt. (Spec 9.1)
		validFrom: elwg ? ELWG_POWER_VALID_FROM : '',
		validUntil: '',
		windows: []
	};
}

function planningDraft(planning: PlanningSettings): PlanningDraft {
	return {
		gridChargeAllowed: planning.grid_charge_allowed,
		marginCt: decimalText(planning.margin_ct),
		roundtripPct: decimalText(planning.roundtrip_pct),
		wearCt: decimalText(planning.wear_ct),
		endValueFactor: decimalText(planning.end_value_factor),
		inertiaCt: decimalText(planning.inertia_ct)
	};
}

function learningDraft(learning: LearningSettings): LearningDraft {
	return { loadModelEnabled: learning.load_model_enabled, haSensors: [...learning.ha_sensors] };
}

/** `wallboxes`: Schlüssel aller Ladestationen aus config.yaml (auch ohne eigenen Namen) */
export function draftFrom(settings: RuntimeSettings, wallboxes: string[] = []): SettingsDraft {
	return {
		tariff: {
			vatPct: decimalText(settings.tariff.vat_pct),
			fixedPriceCt: decimalText(settings.tariff.fixed_price_gross_ct),
			components: settings.tariff.components.map(componentDraft)
		},
		feedIn: Object.entries(settings.feed_in.monthly_ct)
			.sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
			.map(([month, ct]) => ({ month, ct: decimalText(ct) })),
		prices: { ...settings.prices },
		planning: planningDraft(settings.planning),
		learning: learningDraft(settings.learning),
		wallboxNames: {
			...Object.fromEntries(wallboxes.map((key) => [key, ''])),
			...settings.wallbox_names
		},
		comment: ''
	};
}

interface NumberRule {
	/** leer ist ein Fehler (Feld erlaubt kein `null`) */
	required?: boolean;
	/** kleinster erlaubter Wert; darunter „Wert ab …“ */
	min?: number;
	/** Wert muss größer als 0 sein */
	positive?: boolean;
	max?: number;
}

/** Zahl aus dem Text; bei Fehler steht er unter `key`, Ergebnis ist dann `null` */
function readNumber(
	text: string,
	key: string,
	errors: Record<string, string>,
	rule: NumberRule = {}
): number | null {
	const value = parseDecimal(text);
	if (value === 'invalid' || (value === null && rule.required)) {
		errors[key] = NUMBER_ERROR;
		return null;
	}
	if (value === null) return null;
	if (rule.positive && value <= 0) {
		errors[key] = 'Wert größer 0';
		return null;
	}
	if (rule.min !== undefined && value < rule.min) {
		errors[key] = `Wert ab ${rule.min}`;
		return null;
	}
	if (rule.max !== undefined && value > rule.max) {
		errors[key] = `Wert von ${rule.min ?? 0} bis ${rule.max}`;
		return null;
	}
	return value;
}

/** Minuten seit Mitternacht oder `null`, wenn der Text keine Viertelstunde ist */
function quarterHour(text: string): number | null {
	const match = TIME_RE.exec(text.trim());
	if (!match) return null;
	const hours = Number(match[1]);
	const minutes = Number(match[2]);
	return hours < 24 && minutes % 15 === 0 ? hours * 60 + minutes : null;
}

function clockText(minutes: number): string {
	const pad = (n: number) => String(n).padStart(2, '0');
	return `${pad(Math.floor(minutes / 60))}:${pad(minutes % 60)}:00`;
}

function applyWindow(
	draft: WindowDraft,
	prefix: string,
	errors: Record<string, string>
): TimeWindow | null {
	const before = Object.keys(errors).length;
	if (draft.months.length === 0) errors[`${prefix}.months`] = 'Mindestens ein Monat';
	if (draft.weekdays.length === 0) errors[`${prefix}.weekdays`] = 'Mindestens ein Wochentag';
	const start = quarterHour(draft.start);
	if (start === null) errors[`${prefix}.start`] = QUARTER_ERROR;
	// „24:00“ und „00:00“ als Ende heißen beide Tagesende
	const end = draft.end.trim() === '24:00' ? 0 : quarterHour(draft.end);
	if (end === null) errors[`${prefix}.end`] = QUARTER_ERROR;
	else if (start !== null && end !== 0 && end <= start)
		errors[`${prefix}.end`] = 'Ende muss nach dem Beginn liegen (00:00 = Tagesende)';
	const byValue = draft.mode === 'value';
	const amount = readNumber(draft.amount, `${prefix}.${byValue ? 'value_ct' : 'factor'}`, errors, {
		required: true,
		min: byValue ? undefined : 0
	});
	if (Object.keys(errors).length > before || start === null || end === null) return null;
	return {
		months: [...draft.months].sort((a, b) => a - b),
		weekdays: [...draft.weekdays].sort((a, b) => a - b),
		start: clockText(start),
		end: clockText(end),
		factor: byValue ? null : amount,
		value_ct: byValue ? amount : null
	};
}

function applyComponent(
	draft: ComponentDraft,
	prefix: string,
	errors: Record<string, string>
): PriceComponent | null {
	const before = Object.keys(errors).length;
	const name = draft.name.trim();
	if (name === '') errors[`${prefix}.name`] = 'Name eingeben';
	else if (name.length > NAME_MAX) errors[`${prefix}.name`] = `Höchstens ${NAME_MAX} Zeichen`;
	for (const [field, text] of [
		['valid_from', draft.validFrom],
		['valid_until', draft.validUntil]
	] as const) {
		if (text.trim() !== '' && !DATE_RE.test(text.trim()))
			errors[`${prefix}.${field}`] = 'Datum wählen';
	}
	const from = draft.validFrom.trim() || null;
	const until = draft.validUntil.trim() || null;
	if (from && until && DATE_RE.test(from) && DATE_RE.test(until) && from > until)
		errors[`${prefix}.valid_from`] = '„gültig ab“ liegt nach „gültig bis“';

	const base = { id: draft.id, name, vat: draft.vat, valid_from: from, valid_until: until };
	let result: PriceComponent | null = null;
	switch (draft.kind) {
		case 'energy_per_kwh':
		case 'feed_in_per_kwh': {
			// der Börsenpreis hat keinen festen Wert
			const spot = draft.kind === 'energy_per_kwh' && draft.source === 'spot';
			const value = spot ? null : readNumber(draft.value, `${prefix}.value_ct`, errors);
			const windows = draft.windows.map((window, index) =>
				applyWindow(window, `${prefix}.windows.${index}`, errors)
			);
			if (draft.kind === 'energy_per_kwh') {
				const energy: EnergyComponent = {
					...base,
					kind: 'energy_per_kwh',
					source: draft.source,
					value_ct: value,
					windows: windows.filter((w) => w !== null)
				};
				result = energy;
			} else {
				const feedIn: FeedInComponent = {
					...base,
					kind: 'feed_in_per_kwh',
					value_ct: value,
					windows: windows.filter((w) => w !== null)
				};
				result = feedIn;
			}
			break;
		}
		case 'power_per_kw_year': {
			const power: PowerComponent = {
				...base,
				kind: 'power_per_kw_year',
				value_eur: readNumber(draft.value, `${prefix}.value_eur`, errors, { min: 0 }),
				value_above_eur: readNumber(draft.valueAbove, `${prefix}.value_above_eur`, errors, {
					min: 0
				}),
				tier_kw:
					readNumber(draft.tierKw, `${prefix}.tier_kw`, errors, {
						required: true,
						positive: true
					}) ?? 0,
				minimum_kw:
					readNumber(draft.minimumKw, `${prefix}.minimum_kw`, errors, { required: true, min: 0 }) ??
					0
			};
			result = power;
			break;
		}
		case 'fixed_per_month': {
			const fee: MonthlyFeeComponent = {
				...base,
				kind: 'fixed_per_month',
				value_eur: readNumber(draft.value, `${prefix}.value_eur`, errors, { min: 0 })
			};
			result = fee;
			break;
		}
	}
	return Object.keys(errors).length > before ? null : result;
}

/** Gleiche Werte, unabhängig von der Reihenfolge der Schlüssel */
function sameValue(a: unknown, b: unknown): boolean {
	if (Array.isArray(a) || Array.isArray(b)) {
		return (
			Array.isArray(a) &&
			Array.isArray(b) &&
			a.length === b.length &&
			a.every((item, index) => sameValue(item, b[index]))
		);
	}
	if (typeof a === 'object' && typeof b === 'object' && a !== null && b !== null) {
		const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
		return [...keys].every((key) =>
			sameValue((a as Record<string, unknown>)[key], (b as Record<string, unknown>)[key])
		);
	}
	return a === b;
}

/** Ändert sich etwas, das die Bezugspreise berührt (Energiebestandteile, USt.)? */
function importPricesChanged(before: RuntimeSettings['tariff'], after: RuntimeSettings['tariff']) {
	if (before.vat_pct !== after.vat_pct) return true;
	const energy = (components: PriceComponent[]) => components.filter(isEnergy);
	const was = energy(before.components);
	const now = energy(after.components);
	return (
		was.length !== now.length ||
		now.some((component) => {
			const old = was.find((c) => c.id === component.id);
			return old === undefined || !sameValue(old, component);
		})
	);
}

/** Zahl im Bereich [min, max] oder `null` mit Fehlertext unter `key` (leer ist ein Fehler) */
function planningNumber(
	text: string,
	key: string,
	errors: Record<string, string>,
	min: number,
	max: number | null,
	message: string
): number | null {
	const value = parseDecimal(text);
	if (value === null || value === 'invalid' || value < min || (max !== null && value > max)) {
		errors[key] = message;
		return null;
	}
	return value;
}

function applyPlanning(
	draft: PlanningDraft,
	errors: Record<string, string>
): PlanningSettings | null {
	const before = Object.keys(errors).length;
	const planning = {
		grid_charge_allowed: draft.gridChargeAllowed,
		margin_ct: planningNumber(
			draft.marginCt,
			'planning.margin_ct',
			errors,
			0,
			null,
			NON_NEGATIVE_ERROR
		),
		roundtrip_pct: planningNumber(
			draft.roundtripPct,
			'planning.roundtrip_pct',
			errors,
			50,
			100,
			PERCENT_ERROR
		),
		wear_ct: planningNumber(draft.wearCt, 'planning.wear_ct', errors, 0, null, NON_NEGATIVE_ERROR),
		end_value_factor: planningNumber(
			draft.endValueFactor,
			'planning.end_value_factor',
			errors,
			0,
			1,
			FACTOR_ERROR
		),
		inertia_ct: planningNumber(
			draft.inertiaCt,
			'planning.inertia_ct',
			errors,
			0,
			null,
			NON_NEGATIVE_ERROR
		)
	};
	if (Object.keys(errors).length > before) return null;
	return planning as PlanningSettings;
}

function applyLearning(
	draft: LearningDraft,
	errors: Record<string, string>
): Pick<LearningSettings, 'load_model_enabled' | 'ha_sensors'> {
	const sensors = draft.haSensors.map((id) => id.trim());
	if (sensors.length > MAX_HA_SENSORS || new Set(sensors).size !== sensors.length) {
		errors['learning.ha_sensors'] = `Höchstens ${MAX_HA_SENSORS} Sensoren, jeder nur einmal`;
	}
	return { load_model_enabled: draft.loadModelEnabled, ha_sensors: sensors };
}

export function applyDraft(settings: RuntimeSettings, draft: SettingsDraft): DraftResult {
	const errors: Record<string, string> = {};
	const vatPct = readNumber(draft.tariff.vatPct, 'tariff.vat_pct', errors, {
		required: true,
		min: 0,
		max: 100
	});
	const fixedPrice = readNumber(draft.tariff.fixedPriceCt, 'tariff.fixed_price_gross_ct', errors, {
		required: true,
		min: 0
	});
	const components: PriceComponent[] = [];
	draft.tariff.components.forEach((component, index) => {
		const applied = applyComponent(component, `tariff.components.${index}`, errors);
		if (applied) components.push(applied);
	});

	const monthly: Record<string, number> = {};
	draft.feedIn.forEach((row, index) => {
		const month = row.month.trim();
		const text = row.ct.trim();
		if (month === '' && text === '') return; // leere Zeile
		let valid = true;
		if (!MONTH_RE.test(month)) {
			errors[`feedIn.${index}.month`] = 'Monat wählen';
			valid = false;
		} else if (month in monthly) {
			errors[`feedIn.${index}.month`] = 'Monat doppelt';
			valid = false;
		}
		const ct = parseDecimal(text);
		if (ct === null || ct === 'invalid') {
			errors[`feedIn.${index}.ct`] = 'Zahl, z. B. 7,3';
			valid = false;
		} else if (ct < 0) {
			errors[`feedIn.${index}.ct`] = 'Wert ab 0';
			valid = false;
		}
		if (valid && typeof ct === 'number') monthly[month] = ct;
	});

	const planning = applyPlanning(draft.planning, errors);

	const learning = applyLearning(draft.learning, errors);

	const wallboxNames: Record<string, string> = {};
	for (const [key, text] of Object.entries(draft.wallboxNames)) {
		const name = text.trim();
		if (name === '') continue; // ohne eigenen Namen gilt der Typ
		if (name.length > WALLBOX_NAME_MAX) {
			errors[`wallboxNames.${key}`] = `Höchstens ${WALLBOX_NAME_MAX} Zeichen`;
			continue;
		}
		wallboxNames[key] = name;
	}

	if (
		Object.keys(errors).length > 0 ||
		vatPct === null ||
		fixedPrice === null ||
		planning === null
	) {
		return { errors };
	}
	const tariff = {
		...settings.tariff,
		vat_pct: vatPct,
		components,
		fixed_price_gross_ct: fixedPrice
	};
	return {
		settings: {
			...settings,
			prices: { ...settings.prices, ...draft.prices },
			tariff,
			feed_in: { ...settings.feed_in, monthly_ct: monthly },
			planning: { ...settings.planning, ...planning },
			learning: { ...settings.learning, ...learning },
			wallbox_names: wallboxNames
		},
		tariffChanged: importPricesChanged(settings.tariff, tariff)
	};
}

/** Zusammenhängende Nummern als Spannen, z. B. [3, 4, 5, 9] → „Apr–Jun, Okt“ */
function spans(values: number[], names: string[], offset: number): string {
	const sorted = [...new Set(values)].sort((a, b) => a - b);
	const parts: string[] = [];
	for (let i = 0; i < sorted.length;) {
		let j = i;
		while (j + 1 < sorted.length && sorted[j + 1] === sorted[j] + 1) j++;
		const first = names[sorted[i] - offset] ?? String(sorted[i]);
		const last = names[sorted[j] - offset] ?? String(sorted[j]);
		parts.push(i === j ? first : `${first}–${last}`);
		i = j + 1;
	}
	return parts.join(', ');
}

function clock(time: string, end: boolean): string {
	const hhmm = time.slice(0, 5);
	return end && hhmm === '00:00' ? '24:00' : hhmm; // 00:00 als Ende heißt Tagesende
}

/** „Apr–Sep, Mo–So, 10:00–16:00: × 0,8“ */
export function describeWindow(window: TimeWindow): string {
	const months = window.months.length === 12 ? 'ganzjährig' : spans(window.months, MONTHS, 1);
	const days = spans(window.weekdays, WEEKDAYS, 0);
	const time = `${clock(window.start, false)}–${clock(window.end, true)}`;
	const rule =
		window.value_ct !== null
			? `${decimalText(window.value_ct)} ct`
			: window.factor !== null
				? `× ${decimalText(window.factor)}`
				: '';
	return `${months}, ${days}, ${time}${rule ? `: ${rule}` : ''}`;
}

function dateText(date: string): string {
	const [year, month, day] = date.split('-');
	return `${day}.${month}.${year}`;
}

export function validityText(component: PriceComponent): string {
	const { valid_from: from, valid_until: until } = component;
	if (from && until) return `${dateText(from)} bis ${dateText(until)}`;
	if (from) return `ab ${dateText(from)}`;
	if (until) return `bis ${dateText(until)}`;
	return 'immer';
}

/** Fehler einer 422-Antwort auf PUT /api/settings mit den Schlüsseln des Editors */
export function editorFieldErrors(
	detail: unknown,
	settings: RuntimeSettings
): Record<string, string> {
	const months = Object.keys(settings.feed_in.monthly_ct);
	const result: Record<string, string> = {};
	for (const [path, message] of Object.entries(fieldErrors(detail, ['body', 'settings']))) {
		const parts = path.split('.');
		// pydantic nennt bei der Union der Bestandteile die Art im Pfad: components.2.<kind>.value_ct
		if (parts[0] === 'tariff' && parts[1] === 'components' && COMPONENT_KINDS.includes(parts[3])) {
			parts.splice(3, 1);
		}
		let key = parts.join('.');
		if (parts[0] === 'feed_in' && parts[1] === 'monthly_ct' && parts.length >= 3) {
			const index = months.indexOf(parts[2]);
			if (index >= 0) key = `feedIn.${index}.${parts.length > 3 ? 'month' : 'ct'}`;
		} else if (parts[0] === 'wallbox_names' && parts.length >= 2) {
			key = `wallboxNames.${parts[1]}`;
		}
		result[key] ??= message;
	}
	return result;
}

/** Vorschlag für die Neubewertung: vom Monatsbeginn bis heute (Wien) */
export function defaultRepriceRange(now: Date): { first: string; last: string } {
	const today = todayVienna(now);
	return { first: `${today.slice(0, 8)}01`, last: today };
}
