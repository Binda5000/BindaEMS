// Zeitplan des Akkus: Beschriftungen und die Blöcke des Ablaufs
import type {
	BatteryIntentKind,
	PlanReasonCode,
	PlanSlot,
	PlanTrigger,
	PlanVehicle
} from '$lib/api/schemas';
import { expectedLabel, formatNumberMax } from '$lib/format';
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
	vehicles: 'Fahrzeug geändert'
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
	/** SOC am Ende des Slots vor dem Block, beim ersten Block der erste Wert */
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
export function planBlocks(slots: PlanSlot[], vehicles: PlanVehicle[] = []): PlanBlock[] {
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
			socFrom: (index > 0 ? slots[index - 1] : slot).soc_pct,
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

/** „Netzladen 2,3 kW“, „Halten“, „Eigenverbrauch“ */
export function blockLabel(block: PlanBlock): string {
	const label = INTENT_LABELS[block.intent];
	return block.intent === 'NETZLADEN' && block.gridChargeW > 0
		? `${label} ${formatNumberMax(block.gridChargeW / 1000, 1)} kW`
		: label;
}
