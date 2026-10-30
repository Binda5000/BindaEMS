// Akku-Kachel der Übersicht: Ladestand, Leistung, Reserve und der Ausblick aus dem Plan
import type { CoreState, Limits, PlanResponse, PlanSlot } from '$lib/api/schemas';
import {
	DASH,
	formatNumberMax,
	formatPower,
	formatSoc,
	formatTime,
	NBSP,
	weekdayDate
} from '$lib/format';
import { addDays, parseIso, todayVienna } from '$lib/time';
import { IDLE_W } from './flow';
import type { SocQuality } from './socs';

export type BatteryDirection = 'charge' | 'discharge' | 'idle' | 'unknown';

export interface PlanOutlook {
	trend: 'up' | 'down' | 'hold';
	socPct: number;
	/** ISO, Ende des Slots mit dem Extremwert */
	at: string;
}

export interface BatteryView {
	pct: number | null;
	quality: SocQuality;
	kwh: number | null;
	usableKwh: number | null;
	reservePct: number | null;
	/** Betrag der Akkuleistung in W; die Richtung steht in `direction` */
	powerW: number | null;
	direction: BatteryDirection;
	atOrBelowReserve: boolean;
	gridChargeAllowed: boolean | null;
	outlook: PlanOutlook | null;
}

const SLOT_MS = 15 * 60_000;
const DEFAULT_TOLERANCE_PCT = 1;

/**
 * Wohin sich der Akku im Plan entwickelt: Der Lauf beginnt beim ersten Slot, der noch nicht
 * zu Ende ist. Die Richtung gibt die erste Änderung über der Toleranz (gegen den ersten Slot) vor.
 * Der Lauf endet vor dem ersten Slot, der um mehr als die Toleranz gegen die Richtung geht;
 * Ziel ist der erste Slot mit dem Extremwert.
 */
export function planOutlook(
	slots: PlanSlot[],
	nowMs: number,
	tolerancePct: number = DEFAULT_TOLERANCE_PCT
): PlanOutlook | null {
	const upcoming = slots.flatMap((slot) => {
		const start = parseIso(slot.start);
		return start !== null && start + SLOT_MS > nowMs
			? [{ end: start + SLOT_MS, soc: slot.soc_pct }]
			: [];
	});
	const first = upcoming[0];
	if (!first) return null;

	const change = upcoming.find((item) => Math.abs(item.soc - first.soc) > tolerancePct);
	if (!change) return { trend: 'hold', socPct: first.soc, at: new Date(first.end).toISOString() };

	const sign = change.soc > first.soc ? 1 : -1;
	let extreme = first;
	for (const item of upcoming) {
		if ((item.soc - extreme.soc) * sign > 0) extreme = item;
		else if ((extreme.soc - item.soc) * sign > tolerancePct) break;
	}
	return {
		trend: sign > 0 ? 'up' : 'down',
		socPct: extreme.soc,
		at: new Date(extreme.end).toISOString()
	};
}

function direction(watts: number | null): BatteryDirection {
	if (watts === null) return 'unknown';
	if (Math.abs(watts) <= IDLE_W) return 'idle';
	return watts > 0 ? 'charge' : 'discharge';
}

export function batteryView(
	state: CoreState | null | undefined,
	limits: Limits | null | undefined,
	plan: PlanResponse | null | undefined,
	nowMs: number
): BatteryView {
	const soc = state?.signals['battery.soc_pct'];
	const pct = soc && soc.q !== 'invalid' && typeof soc.v === 'number' ? soc.v : null;
	const quality: SocQuality = pct === null ? 'missing' : soc?.q === 'ok' ? 'ok' : 'stale';

	const usableKwh = limits?.battery.usable_kwh ?? null;
	const reservePct = limits?.battery.reserve_soc_pct ?? null;
	const rawPower = state?.derived.battery_w;
	const power = typeof rawPower === 'number' && Number.isFinite(rawPower) ? rawPower : null;
	const current = plan?.plan;

	return {
		pct,
		quality,
		kwh: pct !== null && usableKwh !== null ? (pct * usableKwh) / 100 : null,
		usableKwh,
		reservePct,
		powerW: power === null ? null : Math.abs(power),
		direction: direction(power),
		atOrBelowReserve: pct !== null && reservePct !== null && pct <= reservePct,
		gridChargeAllowed: plan?.grid_charge_allowed ?? null,
		outlook: current?.valid ? planOutlook(current.slots, nowMs) : null
	};
}

/** „um 15:00“ heute, „morgen um 06:00“, danach „am So 11.10. um 06:00“ (Wiener Kalender von `nowMs`) */
function whenText(at: string, nowMs: number): string {
	const time = `um ${formatTime(at)}`;
	const ms = parseIso(at);
	if (ms === null) return time;
	const day = todayVienna(new Date(ms));
	const today = todayVienna(new Date(nowMs));
	if (day === today) return time;
	return day === addDays(today, 1) ? `morgen ${time}` : `am ${weekdayDate(day)} ${time}`;
}

/** „steigt bis 90 % um 15:00“, „sinkt bis 25 % morgen um 06:00“, „hält 55 %“ */
export function outlookText(outlook: PlanOutlook, nowMs: number): string {
	const soc = formatSoc(outlook.socPct);
	if (outlook.trend === 'hold') return `hält ${soc}`;
	const verb = outlook.trend === 'up' ? 'steigt' : 'sinkt';
	return `${verb} bis ${soc} ${whenText(outlook.at, nowMs)}`;
}

/** „lädt 1,00 kW“, „entlädt 800 W“, „ruht“; unbekannt ist ein Strich */
export function powerText(view: BatteryView): string {
	switch (view.direction) {
		case 'charge':
			return `lädt ${formatPower(view.powerW)}`;
		case 'discharge':
			return `entlädt ${formatPower(view.powerW)}`;
		case 'idle':
			return 'ruht';
		case 'unknown':
			return DASH;
	}
}

/** „≈ 16,5 von 30 kWh“; ohne SOC oder nutzbare Kapazität `null` */
export function energyText(view: BatteryView): string | null {
	if (view.kwh === null || view.usableKwh === null) return null;
	return `≈ ${formatNumberMax(view.kwh, 1)} von ${formatNumberMax(view.usableKwh, 1)}${NBSP}kWh`;
}

/** „≈ 4,8 kWh über Reserve“, auf oder unter der Reserve „nichts über Reserve“; ohne Werte `null` */
export function reserveEnergyText(view: BatteryView): string | null {
	if (view.pct === null || view.reservePct === null || view.usableKwh === null) return null;
	if (view.pct <= view.reservePct) return 'nichts über Reserve';
	const kwh = ((view.pct - view.reservePct) * view.usableKwh) / 100;
	return `≈ ${formatNumberMax(kwh, 1)}${NBSP}kWh über Reserve`;
}

/** Beschriftung des Batteriesymbols für Screenreader */
export function batteryLabel(view: BatteryView): string {
	const level = view.pct === null ? 'ohne Messwert' : formatSoc(view.pct);
	const reserve = view.reservePct === null ? '' : `, Reserve ${formatSoc(view.reservePct)}`;
	return `Hausakku ${level}${reserve}`;
}

/** „Netzladen erlaubt“ / „Netzladen aus“; unbekannt `null` */
export function gridChargeText(allowed: boolean | null): string | null {
	if (allowed === null) return null;
	return allowed ? 'Netzladen erlaubt' : 'Netzladen aus';
}
