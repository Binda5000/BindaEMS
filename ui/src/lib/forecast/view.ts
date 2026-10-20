// Aufbereitung für die Seite „Prognosen“: Bezeichnungen, Reihenfolge und Zahlentexte
import type {
	Backtest,
	Horizon,
	ModelFeature,
	QualityDailyRow,
	QualityKind,
	QualityRow
} from '$lib/api/schemas';
import { DASH, NBSP, formatDateTime, formatNumber } from '$lib/format';

export const KIND_LABELS: Record<QualityKind, string> = {
	pv: 'PV',
	load: 'Last (verwendet)',
	load_profile: 'Last Profil',
	load_model: 'Last Modell'
};

export const HORIZON_LABELS: Record<Horizon, string> = {
	h0_6: '0–6 h',
	h6_24: '6–24 h',
	h24: 'über 24 h'
};

/** Zeiträume der Güte in Tagen */
export const PERIODS = [7, 14, 28, 60] as const;

const KIND_ORDER: readonly QualityKind[] = ['pv', 'load', 'load_profile', 'load_model'];
const HORIZON_ORDER: readonly Horizon[] = ['h0_6', 'h6_24', 'h24'];

/** Leistung in W ohne Nachkommastelle, auch ab 1000 W (Vergleich der Zeilen) */
export function watts(value: number): string {
	return `${formatNumber(value, 0)}${NBSP}W`;
}

/** Anteil in % mit einer Nachkommastelle; ohne Wert ein Strich */
export function relativeText(pct: number | null): string {
	return pct === null ? DASH : `${formatNumber(pct, 1)}${NBSP}%`;
}

/** Verbesserung des Modells mit Vorzeichen: „+15 %“, „−4 %“; ohne Wert ein Strich */
export function improvementText(pct: number | null): string {
	if (pct === null) return DASH;
	const rounded = Math.round(pct);
	if (rounded === 0) return `0${NBSP}%`;
	return `${rounded > 0 ? '+' : '−'}${Math.abs(rounded)}${NBSP}%`;
}

export interface QualityView {
	/** eindeutig je Zeile */
	key: string;
	kind: string;
	horizon: string;
	slots: string;
	mae: string;
	bias: string;
	rel: string;
	better: string;
}

/** Zeilen der Güte in der Reihenfolge PV, Last (verwendet), Profil, Modell; je Art nach Vorlauf */
export function qualityRows(rows: readonly QualityRow[]): QualityView[] {
	return [...rows]
		.sort(
			(a, b) =>
				KIND_ORDER.indexOf(a.kind) - KIND_ORDER.indexOf(b.kind) ||
				HORIZON_ORDER.indexOf(a.horizon) - HORIZON_ORDER.indexOf(b.horizon)
		)
		.map((row) => ({
			key: `${row.kind}-${row.horizon}`,
			kind: KIND_LABELS[row.kind],
			horizon: HORIZON_LABELS[row.horizon],
			slots: formatNumber(row.n, 0),
			mae: watts(row.mae_w),
			bias: watts(row.bias_w),
			rel: relativeText(row.rel_mae_pct),
			better: row.kind === 'load_model' ? improvementText(row.improvement_pct) : DASH
		}));
}

export interface DailyPoint {
	date: string;
	mae_w: number;
}

/** MAE je Tag von Profil und Modell, nach Datum */
export function dailySeries(rows: readonly QualityDailyRow[]): {
	profile: DailyPoint[];
	model: DailyPoint[];
} {
	const pick = (kind: QualityKind): DailyPoint[] =>
		rows
			.filter((row) => row.kind === kind)
			.map((row) => ({ date: row.date, mae_w: row.mae_w }))
			.sort((a, b) => a.date.localeCompare(b.date));
	return { profile: pick('load_profile'), model: pick('load_model') };
}

export interface FeatureView {
	name: string;
	label: string;
	/** Anteil am Gewinn in % */
	share: number;
	text: string;
}

/** Merkmale nach Anteil absteigend */
export function featureRows(features: readonly ModelFeature[]): FeatureView[] {
	return features
		.map((feature) => ({
			name: feature.name,
			label: feature.label,
			share: Math.round(feature.importance * 100_000) / 1000,
			text: `${formatNumber(feature.importance * 100, 1)}${NBSP}%`
		}))
		.sort((a, b) => b.share - a.share);
}

/** „09.10.2026 03:15“ */
export function modelStamp(iso: string): string {
	return formatDateTime(iso).replace(', ', ' ');
}

/** „14.08.–09.10.2026 · 5.376 Viertelstunden“ */
export function dataRange(from: string, to: string, samples: number): string {
	const first = formatDateTime(from).slice(0, 6); // „14.08.“
	const last = formatDateTime(to).slice(0, 10); // „09.10.2026“
	return `${first}–${last} · ${formatNumber(samples, 0)} Viertelstunden`;
}

export interface BacktestView {
	weeks: {
		key: string;
		origin: string;
		horizon: string;
		slots: string;
		model: string;
		profile: string;
		better: string;
	}[];
	total: { slots: string; model: string; profile: string; better: string };
}

/** Backtest nach Woche und Vorlauf; „Gesamt“ zählt die Slots aller Wochen */
export function backtestRows(backtest: Backtest): BacktestView {
	const weeks = [...backtest.weeks].sort(
		(a, b) =>
			a.origin.localeCompare(b.origin) ||
			HORIZON_ORDER.indexOf(a.horizon) - HORIZON_ORDER.indexOf(b.horizon)
	);
	const slots = weeks.reduce((sum, week) => sum + week.n, 0);
	return {
		weeks: weeks.map((week) => ({
			key: `${week.origin}-${week.horizon}`,
			origin: formatDateTime(week.origin).slice(0, 10),
			horizon: HORIZON_LABELS[week.horizon],
			slots: formatNumber(week.n, 0),
			model: watts(week.model_mae_w),
			profile: watts(week.profile_mae_w),
			better: improvementText(week.improvement_pct)
		})),
		total: {
			slots: formatNumber(slots, 0),
			model: watts(backtest.total.model_mae_w),
			profile: watts(backtest.total.profile_mae_w),
			better: improvementText(backtest.total.improvement_pct)
		}
	};
}
