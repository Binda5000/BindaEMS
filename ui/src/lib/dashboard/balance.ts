// „Woher – Wohin“: zwei Balken mit den Quellen und dem Verbrauch der Anlage (Vorzeichen wie in `Derived`)
import type { Derived } from '$lib/api/schemas';
import { formatPower, DASH } from '$lib/format';
import { IDLE_W, type FlowConsumer } from './flow';

export type BalanceKind = 'pv' | 'grid' | 'battery' | 'house' | 'wallbox' | 'export';

export interface BalanceSegment {
	id: string;
	label: string;
	kind: BalanceKind;
	/** Betrag in W */
	powerW: number;
	/** Anteil am Balken, 0–1 (an den bekannten Segmenten der Seite) */
	share: number;
}

/** Ein Zweig ohne Messwert; die Liste unter dem Balken nennt ihn mit Strich („PV –“) */
export interface BalanceUnknown {
	id: string;
	label: string;
	kind: BalanceKind;
}

export interface Balance {
	sources: BalanceSegment[];
	sinks: BalanceSegment[];
	/** Summe der Seite; `null`, sobald einer ihrer Zweige unbekannt ist (sonst wäre es eine Teilsumme) */
	sourcesW: number | null;
	sinksW: number | null;
	sourcesUnknown: BalanceUnknown[];
	sinksUnknown: BalanceUnknown[];
}

/** Ein Zweig einer Seite: sein Messwert und die Richtung, in die er auf dieser Seite zählt */
interface Branch extends BalanceUnknown {
	value: number | null | undefined;
	sign: 1 | -1;
}

interface Side {
	segments: BalanceSegment[];
	totalW: number | null;
	unknown: BalanceUnknown[];
}

function known(value: number | null | undefined): value is number {
	return typeof value === 'number' && Number.isFinite(value);
}

function branch(
	id: string,
	label: string,
	kind: BalanceKind,
	value: number | null | undefined,
	sign: 1 | -1
): Branch {
	return { id, label, kind, value, sign };
}

/** Bekannte Zweige über der Ruheschwelle werden Segmente; ist ein Zweig unbekannt, fehlt die Summe. */
function side(branches: Branch[]): Side {
	const present: Omit<BalanceSegment, 'share'>[] = [];
	const unknown: BalanceUnknown[] = [];
	for (const { value, sign, ...item } of branches) {
		if (!known(value)) unknown.push(item);
		else if (value * sign > IDLE_W) present.push({ ...item, powerW: Math.abs(value) });
	}
	const total = present.reduce((sum, entry) => sum + entry.powerW, 0);
	return {
		segments: present.map((entry) => ({ ...entry, share: entry.powerW / total })),
		totalW: unknown.length === 0 ? total : null,
		unknown
	};
}

/**
 * Quellen: PV, Netzbezug, Akku-Entladen. Verbrauch: Haus, Akku-Laden, Wallboxen, Einspeisung.
 * Ruhende Zweige fehlen; PV, Haus und Wallboxen zählen nur in ihre eine Richtung. Unbekannte Zweige
 * stehen in `sourcesUnknown`/`sinksUnknown`, die Summe ihrer Seite ist dann `null`. Wallboxen zählen,
 * soweit der core sie kennt (`wallbox_w`); eine nur in den Einstellungen genannte macht nichts unbekannt.
 */
export function balance(
	derived: Derived | null | undefined,
	wallboxes: Record<string, string>
): Balance {
	if (!derived) {
		return {
			sources: [],
			sinks: [],
			sourcesW: null,
			sinksW: null,
			sourcesUnknown: [],
			sinksUnknown: []
		};
	}

	const sources = side([
		branch('pv', 'PV', 'pv', derived.pv_total_w, 1),
		branch('grid', 'Netz', 'grid', derived.grid_w, 1),
		branch('battery', 'Akku', 'battery', derived.battery_w, -1)
	]);
	const sinks = side([
		branch('house', 'Haus', 'house', derived.house_load_w, 1),
		branch('battery', 'Akku', 'battery', derived.battery_w, 1),
		...Object.keys(derived.wallbox_w)
			.sort()
			.map((key) =>
				branch(`wallbox:${key}`, wallboxes[key] ?? key, 'wallbox', derived.wallbox_w[key], 1)
			),
		branch('export', 'Einspeisung', 'export', derived.grid_w, -1)
	]);
	return {
		sources: sources.segments,
		sinks: sinks.segments,
		sourcesW: sources.totalW,
		sinksW: sinks.totalW,
		sourcesUnknown: sources.unknown,
		sinksUnknown: sinks.unknown
	};
}

/** Segmente erst ab diesem Anteil beschriften; kleinere stehen nur in der Liste darunter */
export const LABEL_MIN_SHARE = 0.15;

/** „Küche 230 W · Obergeschoss 600 W“ für „Im Haus“; ohne Verbraucher `null` */
export function houseText(consumers: FlowConsumer[]): string | null {
	if (consumers.length === 0) return null;
	return consumers
		.map(
			(consumer) =>
				`${consumer.name} ${consumer.powerW === null ? DASH : formatPower(consumer.powerW)}`
		)
		.join(' · ');
}
