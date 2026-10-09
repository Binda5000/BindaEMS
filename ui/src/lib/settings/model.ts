// Einstellungen im UI: Entwurf als Text (deutsche Dezimalzahlen), Prüfung wie der Server,
// Texte für Zeitfenster und Gültigkeit, Zuordnung von Serverfehlern zu den Feldern
import type { PriceComponent, PriceSettings, RuntimeSettings, TimeWindow } from '$lib/api/schemas';
import { fieldErrors } from '$lib/api/errors';
import { todayVienna } from '$lib/time';

export interface SettingsDraft {
	/** Werte der festen Tarifbestandteile nach ID, netto in ct/kWh */
	values: Record<string, string>;
	feedIn: { month: string; ct: string }[];
	prices: PriceSettings;
	comment: string;
}

export type DraftResult =
	{ settings: RuntimeSettings; tariffChanged: boolean } | { errors: Record<string, string> };

const DECIMAL_RE = /^-?\d+(?:[.,]\d+)?$/;
const MONTH_RE = /^\d{4}-(0[1-9]|1[0-2])$/;
const MONTHS = ['Jän', 'Feb', 'Mär', 'Apr', 'Mai', 'Jun', 'Jul', 'Aug', 'Sep', 'Okt', 'Nov', 'Dez'];
const WEEKDAYS = ['Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa', 'So']; // 0 = Montag

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

export function draftFrom(settings: RuntimeSettings): SettingsDraft {
	const values: Record<string, string> = {};
	for (const component of settings.tariff.components) {
		if (component.source === 'fixed') values[component.id] = decimalText(component.value_ct);
	}
	return {
		values,
		feedIn: Object.entries(settings.feed_in.monthly_ct)
			.sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
			.map(([month, ct]) => ({ month, ct: decimalText(ct) })),
		prices: { ...settings.prices },
		comment: ''
	};
}

export function applyDraft(settings: RuntimeSettings, draft: SettingsDraft): DraftResult {
	const errors: Record<string, string> = {};
	let tariffChanged = false;
	const components = settings.tariff.components.map((component) => {
		if (component.source !== 'fixed' || !(component.id in draft.values)) return component;
		const value = parseDecimal(draft.values[component.id]);
		if (value === 'invalid') {
			errors[`values.${component.id}`] = 'Zahl, z. B. 8,11';
			return component;
		}
		if (value === component.value_ct) return component;
		tariffChanged = true;
		return { ...component, value_ct: value };
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

	if (Object.keys(errors).length > 0) return { errors };
	return {
		settings: {
			...settings,
			prices: { ...settings.prices, ...draft.prices },
			tariff: { ...settings.tariff, components },
			feed_in: { ...settings.feed_in, monthly_ct: monthly }
		},
		tariffChanged
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
		let key = path;
		if (parts[0] === 'tariff' && parts[1] === 'components' && parts[3] === 'value_ct') {
			const component = settings.tariff.components[Number(parts[2])];
			if (component) key = `values.${component.id}`;
		} else if (parts[0] === 'feed_in' && parts[1] === 'monthly_ct' && parts.length >= 3) {
			const index = months.indexOf(parts[2]);
			if (index >= 0) key = `feedIn.${index}.${parts.length > 3 ? 'month' : 'ct'}`;
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
