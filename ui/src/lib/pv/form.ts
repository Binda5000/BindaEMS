// Formular für PV-Anlagen und Strings: Entwurf als Text, geprüft mit denselben Regeln wie der Server
import type { PvSource, PvSourceInput } from '$lib/api/schemas';

export interface PvDraft {
	name: string;
	/** ID der Anlage, zu der der String gehört; leer: eigene Anlage */
	parentId: string;
	color: string;
	sourceKind: 'core' | 'ha';
	powerRef: string;
	powerUnit: '' | 'W' | 'kW';
	/** optional, Komma oder Punkt */
	kwp: string;
	sort: string;
}

export type PvDraftErrors = Partial<Record<keyof PvDraft, string>>;

/** Farben zur Auswahl (wie die Reihen der Diagramme) */
export const SWATCHES: readonly { color: string; label: string }[] = [
	{ color: '#d18f00', label: 'Orange' },
	{ color: '#1f6feb', label: 'Blau' },
	{ color: '#8250df', label: 'Violett' },
	{ color: '#0b7a5a', label: 'Grün' },
	{ color: '#6e7781', label: 'Grau' }
];

const MAX_NAME = 60;
const MAX_REF = 200;
const MAX_SORT = 1_000_000;
const MIN_KWP = 0.1;
const MAX_KWP = 1000;
const COLOR_RE = /^#[0-9a-fA-F]{6}$/;
const CORE_REF_RE = /^(pv\.[a-z0-9_]+|pv_dc)\.power_w$/;
const HA_REF_RE = /^[a-z_]+\.[a-z0-9_]+$/;

/** Neuer Eintrag: die erste Farbe, die noch keiner hat; mit `parentId` ein String dieser Anlage */
export function emptyDraft(sources: PvSource[], parentId: number | null = null): PvDraft {
	const used = new Set(sources.map((source) => source.color.toLowerCase()));
	const free = SWATCHES.find((swatch) => !used.has(swatch.color));
	return {
		name: '',
		parentId: parentId === null ? '' : String(parentId),
		color: (free ?? SWATCHES[0]!).color,
		sourceKind: parentId === null ? 'core' : 'ha',
		powerRef: '',
		powerUnit: '',
		kwp: '',
		sort: '0'
	};
}

export function draftFromSource(source: PvSource): PvDraft {
	return {
		name: source.name,
		parentId: source.parent_id === null ? '' : String(source.parent_id),
		color: source.color,
		sourceKind: source.source_kind,
		powerRef: source.power_ref,
		powerUnit: source.power_unit ?? '',
		kwp: source.kwp === null ? '' : String(source.kwp).replace('.', ','),
		sort: String(source.sort)
	};
}

export function draftToInput(draft: PvDraft): { input: PvSourceInput } | { errors: PvDraftErrors } {
	const errors: PvDraftErrors = {};
	const name = draft.name.trim();
	if (!name) errors.name = 'Name fehlt';
	else if (name.length > MAX_NAME) errors.name = `höchstens ${MAX_NAME} Zeichen`;

	const parentText = draft.parentId.trim();
	const parentId = parentText === '' ? null : Number(parentText);
	if (parentId !== null && !(Number.isInteger(parentId) && parentId >= 1)) {
		errors.parentId = 'Anlage ungültig';
	}

	if (!COLOR_RE.test(draft.color)) errors.color = 'Farbe als #rrggbb angeben';

	const ref = draft.powerRef.trim();
	if (ref.length > MAX_REF) {
		errors.powerRef = `höchstens ${MAX_REF} Zeichen`;
	} else if (draft.sourceKind === 'core') {
		if (!CORE_REF_RE.test(ref)) {
			errors.powerRef =
				'core-Signal muss ein PV-Signal sein, z. B. pv.huawei.power_w oder pv_dc.power_w';
		}
	} else if (!HA_REF_RE.test(ref)) {
		errors.powerRef = 'HA-Entität als domain.objekt angeben, z. B. sensor.pv1_leistung';
	}
	if (draft.sourceKind === 'ha' && draft.powerUnit === '') {
		errors.powerUnit = 'Einheit wählen (W oder kW)';
	}

	const kwpText = draft.kwp.trim().replace(',', '.');
	const kwp = kwpText === '' ? null : /^\d+(\.\d+)?$/.test(kwpText) ? Number(kwpText) : Number.NaN;
	if (kwp !== null && !(kwp >= MIN_KWP && kwp <= MAX_KWP)) {
		errors.kwp = 'kWp zwischen 0,1 und 1000 oder leer';
	}

	const sortText = draft.sort.trim();
	const sort = sortText === '' ? 0 : /^[+-]?\d+$/.test(sortText) ? Number(sortText) : Number.NaN;
	if (!Number.isInteger(sort) || Math.abs(sort) > MAX_SORT) {
		errors.sort = 'ganze Zahl zwischen -1000000 und 1000000';
	}

	if (Object.keys(errors).length > 0) return { errors };
	return {
		input: {
			name,
			parent_id: parentId,
			color: draft.color,
			source_kind: draft.sourceKind,
			power_ref: ref,
			// core-Signale sind immer W; der Server lehnt eine Einheit dort ab
			power_unit: draft.sourceKind === 'core' ? null : draft.powerUnit || null,
			kwp,
			sort
		}
	};
}

/** Mögliche Anlagen für „Gehört zu“: nur Anlagen (ein String hat keine Strings), nicht der Eintrag selbst */
export function parentOptions(
	sources: PvSource[],
	editingId: number | null
): { id: number; name: string }[] {
	return sources
		.filter((source) => source.parent_id === null && source.id !== editingId)
		.map((source) => ({ id: source.id, name: source.name }));
}

/** Hat der Eintrag Strings? Dann bleibt er eine Anlage. */
export function hasStrings(sources: PvSource[], id: number | null): boolean {
	return id !== null && sources.some((source) => source.parent_id === id);
}
