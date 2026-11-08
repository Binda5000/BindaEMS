// Karten der Seite „PV-Anlagen“: der Baum von /api/pv (Leistung jetzt, Reihenfolge) und der
// Ertrag von /api/pv/period (Zeitraum), über die ID zusammengeführt
import type { PvNode, PvPeriod, PvResponse, PvSource, StatsPeriod } from '$lib/api/schemas';
import { DASH, NBSP, formatNumber } from '$lib/format';
import { periodParam, type Period } from '$lib/stats/period';

/** „Nicht zugeordnet“ steht erst ab so viel Energie oder Leistung da */
const UNASSIGNED_MIN_KWH = 0.1;
const UNASSIGNED_MIN_W = 50;

/** kWp mit zwei Nachkommastellen: „4,10“ */
export function formatKwp(kwp: number): string {
	return formatNumber(kwp, 2);
}

/** Energie mit einer Nachkommastelle: „23,3 kWh“; ohne Wert „–“ */
export function formatKwh(kwh: number | null | undefined): string {
	return typeof kwh === 'number' ? `${formatNumber(kwh, 1)}${NBSP}kWh` : DASH;
}

/** kWh je kWp mit zwei Nachkommastellen: „5,68“; ohne Wert „–“ */
export function formatPerKwp(value: number | null | undefined): string {
	return typeof value === 'number' ? formatNumber(value, 2) : DASH;
}

/** Woher die Leistung kommt */
export function sourceLabel(source: PvSource): string {
	if (source.source_kind === 'ha') return 'Home Assistant';
	return source.power_ref === 'pv_dc.power_w' ? 'Victron-MPPT (DC)' : 'Victron-Zähler (AC)';
}

/** Adresse der Seite: `/pv?zeitraum=monat&tag=2026-11-08` */
export function pvHref(period: Period, day: string): string {
	return `/pv?zeitraum=${periodParam(period)}&tag=${day}`;
}

/** Beschriftung der Kennzahl „Energie im Zeitraum“ */
export function periodLabel(period: StatsPeriod, running: boolean): string {
	switch (period) {
		case 'day':
			return running ? 'Heute' : 'Am Tag';
		case 'week':
			return running ? 'Diese Woche' : 'In der Woche';
		case 'month':
			return running ? 'Dieser Monat' : 'Im Monat';
		case 'year':
			return running ? 'Dieses Jahr' : 'Im Jahr';
	}
}

/** „ab 18.10.“, wenn der erste Tag mit Wert nach dem Beginn des Zeitraums liegt */
function since(firstKnown: string | null | undefined, first: string): string | null {
	if (!firstKnown || firstKnown <= first) return null;
	const [, month, day] = firstKnown.split('-');
	return `ab ${day}.${month}.`;
}

export interface PvEntry {
	id: number;
	name: string;
	color: string;
	kwp: number | null;
	/** Ertrag im Zeitraum (bekannte Tage) */
	kwh: number | null;
	perKwp: number | null;
	/** warum `kwh` fehlt */
	energyNote: string | null;
	/** „ab 21.09.“: frühe Tage des Zeitraums fehlen */
	since: string | null;
	powerW: number | null;
	/** warum `powerW` fehlt */
	powerNote: string | null;
}

export interface PvStringRow extends PvEntry {
	/** Länge des Balkens: kWh/kWp relativ zum besten String der Anlage; ohne kWp `null` */
	barPct: number | null;
}

export interface PvSystemCard extends PvEntry {
	source: string;
	strings: PvStringRow[];
	/** Summe der Strings im Zeitraum (DC); fehlt ein String, `null` */
	stringsKwh: number | null;
}

function order(a: PvSource, b: PvSource): number {
	return (
		a.sort - b.sort || a.name.localeCompare(b.name, 'de', { sensitivity: 'base' }) || a.id - b.id
	);
}

function nodesById(nodes: PvNode[], found = new Map<number, PvNode>()): Map<number, PvNode> {
	for (const node of nodes) {
		if (node.id !== null) found.set(node.id, node);
		nodesById(node.children, found);
	}
	return found;
}

/** Karten je Anlage nach `sort` und Name, darin die Strings ebenso */
export function cards(tree: PvResponse, period?: PvPeriod): PvSystemCard[] {
	const nodes = nodesById(tree.tree);
	const energy = new Map(period?.sources.map((source) => [source.id, source]));
	const ids = new Set(tree.sources.map((source) => source.id));

	function entry(source: PvSource): PvEntry {
		const node = nodes.get(source.id);
		const found = energy.get(source.id);
		const kwh = found?.sum_kwh ?? null;
		const powerW = node?.power_w ?? null;
		return {
			id: source.id,
			name: source.name,
			color: source.color,
			kwp: source.kwp,
			kwh,
			perKwp: found?.kwh_per_kwp ?? null,
			energyNote: kwh === null ? (found?.note ?? null) : null,
			since: period ? since(found?.first_known, period.first) : null,
			powerW,
			powerNote: powerW === null ? (node?.note ?? null) : null
		};
	}

	const sorted = [...tree.sources].sort(order);
	return sorted
		.filter((source) => source.parent_id === null || !ids.has(source.parent_id))
		.map((system) => {
			const strings = sorted.filter((source) => source.parent_id === system.id).map(entry);
			const best = Math.max(0, ...strings.map((row) => row.perKwp ?? 0));
			const kwhs = strings.map((row) => row.kwh);
			return {
				...entry(system),
				source: sourceLabel(system),
				strings: strings.map((row) => ({
					...row,
					barPct:
						row.perKwp === null || row.kwp === null
							? null
							: best > 0
								? Math.round((row.perKwp / best) * 100)
								: 0
				})),
				stringsKwh:
					kwhs.length > 0 && kwhs.every((kwh) => kwh !== null)
						? kwhs.reduce<number>((sum, kwh) => sum + (kwh ?? 0), 0)
						: null
			};
		});
}

export interface PvHeadline {
	/** PV gesamt im Zeitraum */
	kwh: number | null;
	/** PV gesamt jetzt */
	powerW: number | null;
	/** „davon nicht zugeordnet“; `null`: Zeile entfällt */
	unassignedKwh: number | null;
}

/** Kopfzeile „PV gesamt … · jetzt …“, darunter „davon nicht zugeordnet“, wenn es ins Gewicht fällt */
export function headline(tree: PvResponse, period?: PvPeriod): PvHeadline {
	const rest = tree.tree.find((node) => node.kind === 'unassigned');
	const kwh = period?.unassigned?.sum_kwh ?? null;
	const watts = rest?.power_w ?? null;
	const shown =
		kwh !== null && (kwh >= UNASSIGNED_MIN_KWH || (watts !== null && watts >= UNASSIGNED_MIN_W));
	return {
		kwh: period?.total.sum_kwh ?? null,
		powerW: tree.total_w,
		unassignedKwh: shown ? kwh : null
	};
}
