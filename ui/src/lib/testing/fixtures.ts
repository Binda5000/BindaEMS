// Testdaten aus den Vertragsdateien (nur für Tests), jedes Mal frisch durch das Schema geprüft
import * as v from 'valibot';
import decisionsLatestJson from '$lib/api/contract/decisions-latest.json';
import chargingSessionsJson from '$lib/api/contract/charging-sessions.json';
import decisionsJson from '$lib/api/contract/decisions.json';
import drivingJson from '$lib/api/contract/learning-driving.json';
import learningStatusJson from '$lib/api/contract/learning-load.json';
import learningCandidatesJson from '$lib/api/contract/learning-candidates.json';
import qualityDailyJson from '$lib/api/contract/forecast-quality-daily.json';
import qualityJson from '$lib/api/contract/forecast-quality.json';
import ledgerDaysJson from '$lib/api/contract/ledger-days.json';
import limitsJson from '$lib/api/contract/limits.json';
import settingsJson from '$lib/api/contract/settings.json';
import statsMonthJson from '$lib/api/contract/stats-month.json';
import stateJson from '$lib/api/contract/state.json';
import systemJson from '$lib/api/contract/system.json';
import {
	ChargingSessionSchema,
	DaySummarySchema,
	DecisionsResponseSchema,
	DrivingStatusSchema,
	LatestDecisionsResponseSchema,
	LearningStatusSchema,
	LimitsSchema,
	QualityDailySchema,
	QualitySchema,
	ReadingSchema,
	SensorCandidatesSchema,
	SettingsCurrentSchema,
	StateResponseSchema,
	StatsSchema,
	SystemResponseSchema,
	type ChargingSession,
	type CoreState,
	type DaySummary,
	type Derived,
	type DecisionsResponse,
	type DrivingStatus,
	type DrivingVehicle,
	type LatestDecisionsResponse,
	type LearningStatus,
	type Limits,
	type Quality,
	type QualityDaily,
	type Reading,
	type SensorCandidates,
	type SettingsCurrent,
	type Stats,
	type SystemResponse
} from '$lib/api/schemas';

export function coreState(): CoreState {
	const { state } = v.parse(StateResponseSchema, stateJson);
	if (state === null) throw new Error('state.json enthält keinen Zustand');
	return state;
}

export function derived(overrides: Partial<Derived> = {}): Derived {
	return { ...coreState().derived, ...overrides };
}

export function reading(
	value: number | string | boolean | null,
	q: 'ok' | 'stale' | 'invalid' = 'ok'
): Reading {
	return v.parse(ReadingSchema, { v: value, ts: '2026-10-09T08:00:00+00:00', q });
}

export function limits(): Limits {
	return v.parse(LimitsSchema, limitsJson);
}

export function system(): SystemResponse {
	return v.parse(SystemResponseSchema, systemJson);
}

/** Ein Tag der Abrechnung (09.10. der Demo-Welt), einzelne Felder überschreibbar */
export function daySummary(overrides: Partial<DaySummary> = {}): DaySummary {
	const days = v.parse(v.array(DaySummarySchema), ledgerDaysJson);
	const base = days.find((day) => day.date === '2026-10-09') ?? days[0];
	return { ...base, ...overrides };
}

/** Statistik des Oktobers 2026 aus der Demo-Welt, einzelne Felder überschreibbar */
export function stats(overrides: Partial<Stats> = {}): Stats {
	return { ...v.parse(StatsSchema, statsMonthJson), ...overrides };
}

/** Eine beendete Ladesession der Demo-Welt (die jüngste beendete), Felder überschreibbar */
export function chargingSession(overrides: Partial<ChargingSession> = {}): ChargingSession {
	const sessions = v.parse(v.array(ChargingSessionSchema), chargingSessionsJson);
	const base = sessions.find((session) => !session.open) ?? sessions[0];
	return { ...base, ...overrides };
}

export function settingsCurrent(): SettingsCurrent {
	return v.parse(SettingsCurrentSchema, settingsJson);
}

/** Entitäten der HA-Datenbank zur Sensorauswahl (Demo-Welt) */
export function sensorCandidates(): SensorCandidates {
	return v.parse(SensorCandidatesSchema, learningCandidatesJson);
}

/** Entscheidungen der Demo-Welt (neueste zuerst, mit `cursor`) */
export function decisions(): DecisionsResponse {
	return v.parse(DecisionsResponseSchema, decisionsJson);
}

/** Je Aktor die jüngste Entscheidung der letzten 24 h */
export function latestDecisions(): LatestDecisionsResponse {
	return v.parse(LatestDecisionsResponseSchema, decisionsLatestJson);
}

/** Stand des Lastmodells der Demo-Welt (Modell aktiv, Backtest, Schattenbetrieb) */
export function learningStatus(overrides: Partial<LearningStatus> = {}): LearningStatus {
	return { ...v.parse(LearningStatusSchema, learningStatusJson), ...overrides };
}

/** Prognosegüte der Demo-Welt über 14 Tage */
export function quality(overrides: Partial<Quality> = {}): Quality {
	return { ...v.parse(QualitySchema, qualityJson), ...overrides };
}

/** Güte je Tag der Demo-Welt über 28 Tage */
export function qualityDaily(overrides: Partial<QualityDaily> = {}): QualityDaily {
	return { ...v.parse(QualityDailySchema, qualityDailyJson), ...overrides };
}

/** Fahrverhalten der Demo-Welt: Tesla mit Pendelprofil, e-Golf mit vier Tagen Daten */
export function driving(overrides: Partial<DrivingStatus> = {}): DrivingStatus {
	return { ...v.parse(DrivingStatusSchema, drivingJson), ...overrides };
}

/** Ein Auto aus `driving()`; `changes` überschreiben Felder */
export function drivingVehicle(key: string, changes: Partial<DrivingVehicle> = {}): DrivingVehicle {
	const found = driving().vehicles.find((item) => item.key === key);
	if (!found) throw new Error(`Auto ${key} fehlt in learning-driving.json`);
	return { ...found, ...changes };
}
