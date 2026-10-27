// Kennzahlen-Kacheln der Übersicht: Verlauf der letzten 2 h, Netz-Beschriftung und Tagesenergie
import type { HistoryResponse, Stats } from '$lib/api/schemas';
import { IDLE_W } from './flow';

export const SPARK_WINDOW_MS = 2 * 60 * 60 * 1000;

/** Reihen, die die Übersicht für die Verläufe der Kacheln abfragt */
export const HISTORY_SERIES = ['pv', 'house', 'grid', 'soc.battery'] as const;

/** Punkte einer Reihe aus den letzten 2 h vor `nowMs`, nach Zeit sortiert */
export function sparkPoints(
	history: HistoryResponse | null | undefined,
	series: string,
	nowMs: number
): [number, number][] {
	const points = history?.series[series]?.points ?? [];
	const from = nowMs - SPARK_WINDOW_MS;
	return points.filter(([at]) => at >= from).sort((a, b) => a[0] - b[0]);
}

export function gridLabel(
	gridW: number | null | undefined
): 'Netz · Bezug' | 'Netz · Einspeisung' | 'Netz' {
	if (typeof gridW !== 'number' || !Number.isFinite(gridW) || Math.abs(gridW) <= IDLE_W) {
		return 'Netz';
	}
	return gridW > 0 ? 'Netz · Bezug' : 'Netz · Einspeisung';
}

export interface DayEnergy {
	pvKwh: number | null;
	houseKwh: number | null;
	importKwh: number | null;
	exportKwh: number | null;
}

export function dayEnergy(stats: Stats | null | undefined): DayEnergy {
	const energy = stats?.energy;
	return {
		pvKwh: energy?.pv_kwh ?? null,
		houseKwh: energy?.house_kwh ?? null,
		importKwh: energy?.import_kwh ?? null,
		exportKwh: energy?.export_kwh ?? null
	};
}
