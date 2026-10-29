// Zeitplan des Akkus: Beschriftungen und die Blöcke des Ablaufs
import type {
	BatteryIntentKind,
	PlanReasonCode,
	PlanSlot,
	PlanTrigger,
	PlanVehicle
} from '$lib/api/schemas';
import { expectedLabel, formatNumber, formatNumberMax } from '$lib/format';
import { parseIso } from '$lib/time';

export const INTENT_LABELS: Record<BatteryIntentKind, string> = {
	EIGENVERBRAUCH: 'Eigenverbrauch',
	HALTEN: 'Halten',
	NETZLADEN: 'Netzladen'
};

export const TRIGGER_LABELS: Record<PlanTrigger, string> = {
	start: 'Start',
	slot: 'Viertelstunde',
	prices: 'neue Preise',
	pv_forecast: 'neue PV-Prognose',
	load_forecast: 'neue Lastprognose',
	soc: 'SOC weicht ab',
	month_peak: 'neue Monatsspitze',
	settings: 'Einstellungen geändert',
	manual: 'Neu planen',
	vehicles: 'Fahrzeug geändert',
	thermal: 'Klimaanlage geändert'
};

export const SOURCE_LABELS = { optimizer: 'Optimierer', fallback: 'Ersatzplan' } as const;

const SLOT_MS = 900_000;
/** Zusätze zum Hauptcode eines Slots */
const SUFFIX_CODES: readonly PlanReasonCode[] = ['GESCHAETZT', 'ERSATZPLAN'];

export interface PlanBlock {
	start: string;
	end: string;
	intent: PlanSlot['intent'];
	gridChargeW: number;
	/** SOC am Ende des Slots vor dem Block, beim ersten Block der Start-SOC des Plans (sonst das Ende des ersten Slots) */
	socFrom: number;
	socTo: number;
	text: string;
	/** „{Name} erwartet ab {HH:MM}“ für die erwarteten Autos, deren Ankunft in den Block fällt */
	expected: string[];
}

/** Hauptcode eines Slots: der erste Code, der nicht nur „geschätzt“ oder „Ersatzplan“ sagt */
function mainCode(slot: PlanSlot): string {
	return slot.codes.find((code) => !SUFFIX_CODES.includes(code)) ?? slot.codes[0] ?? '';
}

/** Ende der Viertelstunde im Zeitformat des Starts (der Server schreibt `+00:00`) */
function endOf(slot: PlanSlot): string {
	const start = parseIso(slot.start);
	if (start === null) return slot.start;
	const end = new Date(start + SLOT_MS).toISOString();
	if (slot.start.endsWith('+00:00')) return end.replace(/\.\d{3}Z$/, '+00:00');
	return slot.start.includes('.') ? end : end.replace(/\.\d{3}Z$/, 'Z');
}

/**
 * Aufeinanderfolgende Slots mit gleicher Absicht, gleichem Netzladen und gleichem Hauptcode. Ein
 * erwartetes Auto (Plan 5b) hängt am Block seiner Ankunft, eine Ankunft vor dem Plan am ersten Block;
 * liegt die Ankunft hinter dem Plan, bleibt es weg.
 */
export function planBlocks(
	slots: PlanSlot[],
	vehicles: PlanVehicle[] = [],
	socStartPct: number | null = null
): PlanBlock[] {
	const blocks: PlanBlock[] = [];
	let key = '';
	slots.forEach((slot, index) => {
		const slotKey = `${slot.intent}|${slot.grid_charge_w}|${mainCode(slot)}`;
		const last = blocks.at(-1);
		if (last && slotKey === key) {
			last.end = endOf(slot);
			last.socTo = slot.soc_pct;
			return;
		}
		key = slotKey;
		blocks.push({
			start: slot.start,
			end: endOf(slot),
			intent: slot.intent,
			gridChargeW: slot.grid_charge_w,
			socFrom: index > 0 ? slots[index - 1]!.soc_pct : (socStartPct ?? slot.soc_pct),
			socTo: slot.soc_pct,
			text: slot.text,
			expected: []
		});
	});
	for (const car of vehicles) {
		const arrival = car.expected && car.arrival_at !== null ? parseIso(car.arrival_at) : null;
		if (arrival === null) continue;
		const block =
			arrival < (parseIso(blocks[0]?.start ?? '') ?? Infinity)
				? blocks[0]
				: blocks.find((item) => arrival < (parseIso(item.end) ?? -Infinity));
		block?.expected.push(expectedLabel(car.name, car.arrival_at!));
	}
	return blocks;
}

/** Ab dieser Änderung in % zeigt `socRange` eine Nachkommastelle (darunter ist es Rauschen) */
const SOC_DECIMAL_MIN_PCT = 0.05;

/**
 * „19 → 20“: ganzzahlig. Ändert sich der SOC im Block um weniger als einen Prozentpunkt (aber
 * merklich), würde die Rundung die Änderung verschlucken oder verzerren („19 → 19“): dann mit einer
 * Nachkommastelle („19,2 → 19,9“).
 */
export function socRange(from: number, to: number): string {
	const change = Math.abs(to - from);
	const digits = change >= SOC_DECIMAL_MIN_PCT - 1e-9 && change < 1 ? 1 : 0;
	return `${formatNumber(from, digits)} → ${formatNumber(to, digits)}`;
}

/** „Netzladen 2,3 kW“, „Halten“, „Eigenverbrauch“ (die kW nur beim Netzladen mit Leistung) */
export function intentLabel(intent: BatteryIntentKind, gridChargeW: number): string {
	const label = INTENT_LABELS[intent];
	return intent === 'NETZLADEN' && gridChargeW > 0
		? `${label} ${formatNumberMax(gridChargeW / 1000, 1)} kW`
		: label;
}

/** „Netzladen 2,3 kW“, „Halten“, „Eigenverbrauch“ */
export function blockLabel(block: PlanBlock): string {
	return intentLabel(block.intent, block.gridChargeW);
}
