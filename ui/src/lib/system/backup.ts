// Anzeigetexte zur USV-Reichweite und zu den Netzausfällen (GET /api/backup)
import { formatNumber, formatNumberMax, NBSP } from '$lib/format';

/**
 * „≈ 7,5 h“ unter 10 h, sonst „≈ 31 h“ (wie `hours_text` im Server); ohne Wert `null`.
 * Kaufmännisch gerundet: erst auf Zehntel, ab 10 h (auch nach dem Runden) ganze Stunden.
 */
export function hoursText(h: number | null): string | null {
	if (h === null) return null;
	const tenths = Math.floor(h * 10 + 0.5) / 10;
	const value =
		tenths > 0 && tenths < 10 ? formatNumber(tenths, 1) : formatNumber(Math.floor(h + 0.5), 0);
	return `≈ ${value}${NBSP}h`;
}

/** „30 s“, „14 min“, „2 h 5 min“ */
export function durationText(seconds: number): string {
	if (seconds < 60) return `${Math.round(seconds)}${NBSP}s`;
	const minutes = Math.round(seconds / 60);
	if (minutes < 60) return `${minutes}${NBSP}min`;
	const rest = minutes % 60;
	const hours = `${Math.floor(minutes / 60)}${NBSP}h`;
	return rest === 0 ? hours : `${hours} ${rest}${NBSP}min`;
}

/** „3.11.“ für ein Datum `YYYY-MM-DD` */
export function shortDate(date: string): string {
	const [, month, day] = date.split('-').map(Number);
	return `${day}.${month}.`;
}

/** „62 → 60,5 % (min 60,5 %)“; fehlende Werte werden ausgelassen, ohne alle `null` */
export function socText(
	start: number | null,
	end: number | null,
	min: number | null
): string | null {
	const pct = (value: number) => formatNumberMax(value, 1);
	if (start === null && end === null) return min === null ? null : `min ${pct(min)}${NBSP}%`;
	const path = [start, end].map((value) => (value === null ? '–' : pct(value))).join(' → ');
	return `${path}${NBSP}%${min === null ? '' : ` (min ${pct(min)}${NBSP}%)`}`;
}
