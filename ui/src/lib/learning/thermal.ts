// Wärmemodell und Vorheizen/Vorkühlen (Plan 5c): Karte „Wärmemodell“ auf „Prognosen“, Karte
// „Vorheizen und Vorkühlen“ im Zeitplan und das Ende des Planwerts für die Karte „Klimaanlage“.
//
// Vorzeichen: Plan und core rechnen in Stufen, positiv heißt mehr Leistung der Klimaanlage. Die
// Anzeige zeigt stattdessen den Versatz des Sollwerts, den der core sendet (wie der HA-Sensor
// „Geplanter Versatz“): beim Heizen +Stufen × 0,5 K (mehr Leistung = wärmer), beim Kühlen
// −Stufen × 0,5 K (mehr Leistung = kühler). Ohne Heizen oder Kühlen gibt es keinen Versatz.
import type {
	IntentPlan,
	PlanSlot,
	PlanThermal,
	PlanThermalMode,
	ThermalModel,
	ThermalModeStatus
} from '$lib/api/schemas';
import {
	DASH,
	NBSP,
	formatCents,
	formatDayMonthTime,
	formatKelvinShift,
	formatNumber
} from '$lib/format';
import { parseIso } from '$lib/time';

/** Ein Schritt des Versatzes in K (`STEP_K` der app, `max_offset_k` in Vielfachen davon) */
export const THERMAL_STEP_K = 0.5;

const SLOT_MS = 900_000;

export const MODE_LABELS: Record<PlanThermalMode, string> = {
	heizen: 'Heizen',
	kuehlen: 'Kühlen',
	lueften: 'Lüften',
	aus: 'Aus',
	unbekannt: 'unbekannt'
};

/**
 * Versatz des Sollwerts in K zu `steps` Stufen in der Betriebsart `mode` (Vorzeichen siehe oben);
 * `null` ohne Stufen oder ohne Heizen und Kühlen. 0 Stufen geben 0, nie „-0“.
 */
export function plannedOffsetK(
	steps: number | null | undefined,
	mode: string | null | undefined
): number | null {
	if (steps === null || steps === undefined) return null;
	if (steps === 0) return 0;
	if (mode === 'heizen') return steps * THERMAL_STEP_K;
	if (mode === 'kuehlen') return -steps * THERMAL_STEP_K;
	return null;
}

// --- Karte „Wärmemodell“ ---------------------------------------------------------------------

export interface ThermalModelRow {
	label: string;
	value: string;
}

export interface ThermalModeView {
	mode: ThermalModeStatus['mode'];
	/** „Heizen · 21 Tage“, ohne Stand nur „Heizen“ */
	head: string;
	rows: ThermalModelRow[];
	/** „Modell taugt.“ oder der Grund (fertiger Satz des Servers) */
	state: string;
	usable: boolean;
}

function number(value: number | null, digits: number, unit: string): string {
	return value === null ? DASH : `${formatNumber(value, digits)}${NBSP}${unit}`;
}

/** „2,4 kWh Strom je K“; mit Versatz-Ereignissen „2,4 kWh je K (aus 4 Versatz-Ereignissen, Modell 3,1)“ */
function storage(model: ThermalModel): string {
	if (model.c_kwh_per_k === null) return DASH;
	if (model.events === 0) return `${number(model.c_kwh_per_k, 1, 'kWh')} Strom je K`;
	const events = model.events === 1 ? '1 Versatz-Ereignis' : `${model.events} Versatz-Ereignissen`;
	const base = model.c_model_kwh_per_k === null ? DASH : formatNumber(model.c_model_kwh_per_k, 1);
	return `${number(model.c_kwh_per_k, 1, 'kWh')} je K (aus ${events}, Modell ${base})`;
}

/** Kopf, Zeilen und Zustand einer Betriebsart der Karte „Wärmemodell“ */
export function thermalModeView(status: ThermalModeStatus): ThermalModeView {
	const label = MODE_LABELS[status.mode];
	const model = status.model;
	const rows: ThermalModelRow[] =
		model === null
			? []
			: [
					{
						label: 'Grundleistung',
						value:
							model.u_w_per_k === null
								? DASH
								: `${number(model.u_w_per_k, 0, 'W')} je K Temperaturdifferenz`
					},
					{ label: 'Zeitkonstante', value: number(model.tau_h, 0, 'h') },
					{ label: 'Speicher', value: storage(model) },
					{ label: 'Auskühl-Phasen', value: String(model.episodes) },
					{
						label: 'Anpassung',
						value: model.r2 === null ? DASH : `R² ${formatNumber(model.r2, 2)}`
					}
				];
	return {
		mode: status.mode,
		head:
			model === null ? label : `${label} · ${model.days === 1 ? '1 Tag' : `${model.days} Tage`}`,
		rows,
		state: status.text,
		usable: status.usable
	};
}

/** Fuß der Karte: „Berechnet am 09.10. 03:50.“; ohne Stand `null` */
export function computedText(computedAt: string | null): string | null {
	return computedAt === null ? null : `Berechnet am ${formatDayMonthTime(computedAt)}.`;
}

// --- Zeitplan ----------------------------------------------------------------------------------

/** Die Zeile über der Tabelle: aktiv, Vorschau oder der Grund ohne Verschiebung */
export function planThermalLine(thermal: PlanThermal): string {
	const saving = formatCents(thermal.saving_ct);
	if (thermal.active) return `Vorheizen/Vorkühlen aktiv: spart etwa ${saving}.`;
	switch (thermal.reason) {
		case 'ok':
			return `Vorschau: Vorheizen/Vorkühlen würde etwa ${saving} sparen.`;
		case 'off':
			return 'Vorheizen und Vorkühlen ist aus.';
		case 'no_model':
			return `Kein taugliches Wärmemodell für ${MODE_LABELS[thermal.mode]}.`;
		case 'mode':
			return 'Die Klimaanlage heizt oder kühlt gerade nicht.';
		case 'not_worth':
			return 'Verschieben lohnt sich in diesem Plan nicht.';
		case 'solver':
			return 'Die Verschiebung ließ sich nicht berechnen.';
	}
}

/** Spalte „Klima“: „+1,0 K“, „−0,5 K“; ohne Verschiebung (0 oder keine Stufen) „–“ */
export function thermalShiftText(steps: number | null | undefined, mode: string): string {
	const k = plannedOffsetK(steps, mode);
	return k === null || k === 0 ? DASH : formatKelvinShift(k);
}

export interface ThermalRun {
	start: string;
	end: string;
	/** Text der Spalte „Klima“ */
	shift: string;
}

/**
 * Zeilen der Tabelle: aufeinanderfolgende Slots mit gleichem Text der Spalte „Klima“. Trägt kein
 * Slot Stufen (der Plan hat keine Verschiebung), gibt es keine Zeilen.
 */
export function thermalRuns(slots: PlanSlot[], mode: string): ThermalRun[] {
	if (!slots.some((slot) => slot.thermal_steps !== null)) return [];
	const runs: ThermalRun[] = [];
	for (const slot of slots) {
		const start = parseIso(slot.start);
		if (start === null) continue;
		const end = new Date(start + SLOT_MS).toISOString();
		const shift = thermalShiftText(slot.thermal_steps, mode);
		const last = runs.at(-1);
		if (last && last.shift === shift && parseIso(last.end) === start) {
			last.end = end;
		} else {
			runs.push({ start: slot.start, end, shift });
		}
	}
	return runs;
}

// --- Karte „Klimaanlage“ --------------------------------------------------------------------

/**
 * Bis wann der Planwert gilt: das Ende der Slots ab dem laufenden mit denselben Stufen im Plan,
 * den der core ausführt (aus der Absicht), höchstens bis zu dessen `valid_until`. `null` ohne Plan
 * oder ohne Slot für jetzt.
 */
export function plannedUntil(
	plan: Pick<IntentPlan, 'valid_until' | 'slots'> | null | undefined,
	now: Date
): string | null {
	if (!plan) return null;
	const at = now.getTime();
	const starts = plan.slots.map((slot) => parseIso(slot.start));
	const index = starts.findIndex((start) => start !== null && start <= at && at < start + SLOT_MS);
	if (index < 0) return null;
	const steps = plan.slots[index]!.thermal_steps ?? null;
	let end = starts[index]! + SLOT_MS;
	for (let next = index + 1; next < plan.slots.length; next++) {
		if (starts[next] !== end || (plan.slots[next]!.thermal_steps ?? null) !== steps) break;
		end += SLOT_MS;
	}
	const validUntil = parseIso(plan.valid_until);
	if (validUntil !== null) end = Math.min(end, validUntil);
	return new Date(end).toISOString();
}
