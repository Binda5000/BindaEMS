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

const SPARK_WIDTH = 100;
const SPARK_TOP = 2;
const SPARK_BOTTOM = 22; // 24 hoch, 2 Rand für die Linienstärke

function trim(value: number): number {
	return Number(value.toFixed(2));
}

/** Punkte der Polylinie (`x,y x,y …`) auf 100 × 24, größter Wert oben; unter zwei Punkten `null` */
export function sparkLine(points: [number, number][]): string | null {
	if (points.length < 2) return null;
	const times = points.map(([at]) => at);
	const values = points.map(([, value]) => value);
	const t0 = Math.min(...times);
	const timeSpan = Math.max(...times) - t0;
	const v0 = Math.min(...values);
	const valueSpan = Math.max(...values) - v0;
	return points
		.map(([at, value]) => {
			const x = timeSpan > 0 ? ((at - t0) / timeSpan) * SPARK_WIDTH : 0;
			const y =
				valueSpan > 0
					? SPARK_BOTTOM - ((value - v0) / valueSpan) * (SPARK_BOTTOM - SPARK_TOP)
					: (SPARK_TOP + SPARK_BOTTOM) / 2;
			return `${trim(x)},${trim(y)}`;
		})
		.join(' ');
}
