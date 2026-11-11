// Schnee auf den Modulen (Seite „Prognosen“): Zustand als Satz, gekürzte Slots, Knöpfe
import type { ForecastSlot, ForecastSnow, SnowOverride } from '$lib/api/schemas';
import { formatDayMonthTime, formatTime, NBSP } from '$lib/format';
import { parseIso, todayVienna } from '$lib/time';

/** Unter dieser Differenz (W) gilt ein Slot als ungekürzt (Rundung) */
const CUT_MIN_W = 0.5;

export const OVERRIDE_TEXTS: Record<SnowOverride, string> = {
	covered: 'Von Hand gemeldet: Schnee auf den Modulen.',
	free: 'Von Hand gemeldet: Module sind frei.'
};

/** „seit“ in Wiener Zeit; liegt es vor heute, mit dem Tag */
function sinceText(iso: string, now: Date): string {
	const ms = parseIso(iso);
	if (ms !== null && todayVienna(new Date(ms)) !== todayVienna(now)) return formatDayMonthTime(iso);
	return formatTime(iso);
}

/** „vermutet, 10 % der Prognose, seit 07:15“; ohne Kürzung jetzt „frei“ */
export function snowText(snow: ForecastSnow, now: Date): string {
	if (snow.state === 'frei') return 'frei';
	const parts: string[] = [snow.state, `${snow.factor_pct}${NBSP}% der Prognose`];
	if (snow.since !== null) parts.push(`seit ${sinceText(snow.since, now)}`);
	return parts.join(', ');
}

/** Ist mindestens ein Slot gekürzt (jetzt oder wegen künftigen Schneefalls)? */
export function isCut(slots: ForecastSlot[]): boolean {
	return slots.some((slot) => slot.uncut_p50_w - slot.p50_w > CUT_MIN_W);
}
