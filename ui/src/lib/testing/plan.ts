// Pläne für die Tests der Zeitplan-Seite: der Vertrag aus plan.json, dazu von Hand gebaute Slots
import * as v from 'valibot';
import planJson from '$lib/api/contract/plan.json';
import {
	PlanResponseSchema,
	type BatteryIntentKind,
	type Plan,
	type PlanReasonCode,
	type PlanResponse,
	type PlanSlot
} from '$lib/api/schemas';

/** Demo-Tag (Wien = UTC + 2) */
export const DAY = '2026-10-09';
/** Beginn des Demo-Plans */
export const NOW = '2026-10-09T08:00:00+00:00';

/** UTC-Zeitstempel für eine lokale Uhrzeit des Demo-Tages (Sommerzeit: UTC + 2) */
export function iso(local: string): string {
	const [hour, minute] = local.split(':').map(Number);
	return `${DAY}T${String((hour ?? 0) - 2).padStart(2, '0')}:${String(minute).padStart(2, '0')}:00+00:00`;
}

export function slot(
	local: string,
	intent: BatteryIntentKind,
	gridChargeW: number,
	code: PlanReasonCode,
	changes: Partial<PlanSlot> = {}
): PlanSlot {
	return {
		start: iso(local),
		intent,
		grid_charge_w: gridChargeW,
		soc_pct: 50,
		pv_w: 0,
		load_w: 1000,
		import_w: 1000,
		export_w: 0,
		battery_w: 0,
		import_ct: 30,
		feed_in_ct: 7,
		estimated: false,
		budget_kw: null,
		support_soc_pct: null,
		ev_w: {},
		codes: [code],
		text: `Satz ${code}`,
		...changes
	};
}

export const RESPONSE: PlanResponse = v.parse(PlanResponseSchema, planJson);
export const PLAN: Plan = RESPONSE.plan!;

/** Der Demo-Plan mit Halten und Netzladen mittendrin und einem geschätzten Preis am Ende */
export function planWithWindows(): Plan {
	const slots = PLAN.slots.map((s, index): PlanSlot => {
		if (index >= 4 && index < 6) {
			return { ...s, intent: 'HALTEN', codes: ['HALTEN_FUER_SPITZE'], text: 'Akku hält.' };
		}
		if (index >= 6 && index < 8) {
			return {
				...s,
				intent: 'NETZLADEN',
				grid_charge_w: 2300,
				codes: ['NETZLADEN_GUENSTIG'],
				text: 'Akku lädt 2,3 kW aus dem Netz.'
			};
		}
		return index >= PLAN.slots.length - 4 ? { ...s, estimated: true } : s;
	});
	return { ...PLAN, slots };
}
