// „Woher – Wohin“: zwei Balken mit den Quellen und dem Verbrauch der Anlage (Vorzeichen wie in `Derived`)
import type { Derived } from '$lib/api/schemas';
import { IDLE_W } from './flow';

export type BalanceKind = 'pv' | 'grid' | 'battery' | 'house' | 'wallbox' | 'export';

export interface BalanceSegment {
	id: string;
	label: string;
	kind: BalanceKind;
	/** Betrag in W */
	powerW: number;
	/** Anteil am Balken, 0–1 */
	share: number;
}

export interface Balance {
	sources: BalanceSegment[];
	sinks: BalanceSegment[];
	/** `null`, solange kein Wert der Seite bekannt ist */
	sourcesW: number | null;
	sinksW: number | null;
}

type Entry = Omit<BalanceSegment, 'share'>;

function known(value: number | null | undefined): value is number {
	return typeof value === 'number' && Number.isFinite(value);
}

/** Der Betrag, wenn `value` in die gewünschte Richtung über der Ruheschwelle liegt, sonst `null`. */
function beyondIdle(value: number | null | undefined, sign: 1 | -1): number | null {
	return known(value) && value * sign > IDLE_W ? Math.abs(value) : null;
}

function side(entries: (Entry | null)[], anyKnown: boolean): [BalanceSegment[], number | null] {
	const present = entries.filter((entry): entry is Entry => entry !== null);
	const total = present.reduce((sum, entry) => sum + entry.powerW, 0);
	const segments = present.map((entry) => ({ ...entry, share: entry.powerW / total }));
	return [segments, anyKnown ? total : null];
}

function entry(
	id: string,
	label: string,
	kind: BalanceKind,
	value: number | null | undefined,
	sign: 1 | -1
): Entry | null {
	const powerW = beyondIdle(value, sign);
	return powerW === null ? null : { id, label, kind, powerW };
}

/**
 * Quellen: PV, Netzbezug, Akku-Entladen. Verbrauch: Haus, Akku-Laden, Wallboxen, Einspeisung.
 * Ruhende Zweige fehlen; PV, Haus und Wallboxen zählen nur in ihre eine Richtung.
 */
export function balance(
	derived: Derived | null | undefined,
	wallboxes: Record<string, string>
): Balance {
	if (!derived) return { sources: [], sinks: [], sourcesW: null, sinksW: null };

	const keys = [...new Set([...Object.keys(wallboxes), ...Object.keys(derived.wallbox_w)])].sort();
	const wallboxW = keys.map((key) => derived.wallbox_w[key]);

	const [sources, sourcesW] = side(
		[
			entry('pv', 'PV', 'pv', derived.pv_total_w, 1),
			entry('grid', 'Netz', 'grid', derived.grid_w, 1),
			entry('battery', 'Akku', 'battery', derived.battery_w, -1)
		],
		[derived.pv_total_w, derived.grid_w, derived.battery_w].some(known)
	);
	const [sinks, sinksW] = side(
		[
			entry('house', 'Haus', 'house', derived.house_load_w, 1),
			entry('battery', 'Akku', 'battery', derived.battery_w, 1),
			...keys.map((key) =>
				entry(`wallbox:${key}`, wallboxes[key] ?? key, 'wallbox', derived.wallbox_w[key], 1)
			),
			entry('export', 'Einspeisung', 'export', derived.grid_w, -1)
		],
		[derived.house_load_w, derived.battery_w, derived.grid_w, ...wallboxW].some(known)
	);
	return { sources, sinks, sourcesW, sinksW };
}
