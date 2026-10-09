// Formular für Verbraucher: Entwurf als Text, geprüft mit denselben Regeln wie der Server
import type { Consumer, ConsumerInput } from '$lib/api/schemas';

export interface ConsumerDraft {
	name: string;
	group: string;
	parentId: string;
	color: string;
	sourceKind: 'core' | 'ha';
	powerRef: string;
	powerUnit: '' | 'W' | 'kW';
	sort: string;
}

export type DraftErrors = Partial<Record<keyof ConsumerDraft, string>>;

const MAX_TEXT = 60;
const MAX_REF = 200;
const MAX_SORT = 1_000_000;
const COLOR_RE = /^#[0-9a-fA-F]{6}$/;
const CORE_REF_RE = /^[a-z0-9_]+(\.[a-z0-9_]+)*\.power_w$/;
const HA_REF_RE = /^[a-z_]+\.[a-z0-9_]+$/;

export function emptyDraft(): ConsumerDraft {
	return {
		name: '',
		group: '',
		parentId: '',
		color: '#4f8cff',
		sourceKind: 'core',
		powerRef: '',
		powerUnit: '',
		sort: '0'
	};
}

export function draftFromConsumer(consumer: Consumer): ConsumerDraft {
	return {
		name: consumer.name,
		group: consumer.group ?? '',
		parentId: consumer.parent_id === null ? '' : String(consumer.parent_id),
		color: consumer.color,
		sourceKind: consumer.source_kind,
		powerRef: consumer.power_ref,
		powerUnit: consumer.power_unit ?? '',
		sort: String(consumer.sort)
	};
}

export function draftToInput(
	draft: ConsumerDraft
): { input: ConsumerInput } | { errors: DraftErrors } {
	const errors: DraftErrors = {};
	const name = draft.name.trim();
	if (!name) errors.name = 'Name fehlt';
	else if (name.length > MAX_TEXT) errors.name = `höchstens ${MAX_TEXT} Zeichen`;

	const group = draft.group.trim();
	if (group.length > MAX_TEXT) errors.group = `höchstens ${MAX_TEXT} Zeichen`;

	const parentText = draft.parentId.trim();
	const parentId = parentText === '' ? null : Number(parentText);
	if (parentId !== null && !(Number.isInteger(parentId) && parentId >= 1)) {
		errors.parentId = 'Elternelement ungültig';
	}

	if (!COLOR_RE.test(draft.color)) errors.color = 'Farbe als #rrggbb angeben';

	const ref = draft.powerRef.trim();
	if (ref.length > MAX_REF) {
		errors.powerRef = `höchstens ${MAX_REF} Zeichen`;
	} else if (draft.sourceKind === 'core') {
		if (!CORE_REF_RE.test(ref)) {
			errors.powerRef = 'core-Signal muss auf .power_w enden, z. B. load.obergeschoss.power_w';
		}
	} else if (!HA_REF_RE.test(ref)) {
		errors.powerRef = 'HA-Entität als domain.objekt angeben, z. B. sensor.kueche';
	}
	if (draft.sourceKind === 'ha' && draft.powerUnit === '') {
		errors.powerUnit = 'Einheit wählen (W oder kW)';
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
			group: group || null,
			parent_id: parentId,
			color: draft.color,
			source_kind: draft.sourceKind,
			power_ref: ref,
			// core-Signale sind immer W; der Server lehnt eine Einheit dort ab
			power_unit: draft.sourceKind === 'core' ? null : draft.powerUnit || null,
			sort
		}
	};
}

/** Mögliche Elternelemente: ohne den bearbeiteten Verbraucher und seine Nachkommen (kein Kreis). */
export function parentOptions(
	consumers: Consumer[],
	editingId: number | null
): { id: number; name: string }[] {
	const excluded = new Set<number>();
	if (editingId !== null) {
		excluded.add(editingId);
		let grew = true;
		while (grew) {
			grew = false;
			for (const consumer of consumers) {
				if (
					consumer.parent_id !== null &&
					excluded.has(consumer.parent_id) &&
					!excluded.has(consumer.id)
				) {
					excluded.add(consumer.id);
					grew = true;
				}
			}
		}
	}
	return consumers
		.filter((consumer) => !excluded.has(consumer.id))
		.map((consumer) => ({ id: consumer.id, name: consumer.name }));
}
