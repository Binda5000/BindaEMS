// Ladestände von Akku und Fahrzeugen mit ihrer Qualität (veraltet oder fehlend, nie 0 %)
import type { CoreState, Limits } from '$lib/api/schemas';

export type SocQuality = 'ok' | 'stale' | 'missing';

export interface SocEntry {
	id: string;
	label: string;
	pct: number | null;
	quality: SocQuality;
}

function entry(
	state: CoreState | null | undefined,
	id: string,
	label: string,
	signal: string
): SocEntry {
	const reading = state?.signals[signal];
	if (!reading || reading.q === 'invalid' || typeof reading.v !== 'number') {
		return { id, label, pct: null, quality: 'missing' };
	}
	return { id, label, pct: reading.v, quality: reading.q === 'ok' ? 'ok' : 'stale' };
}

export function socEntries(
	state: CoreState | null | undefined,
	limits: Limits | null | undefined
): SocEntry[] {
	return [
		entry(state, 'battery', 'Akku', 'battery.soc_pct'),
		...Object.entries(limits?.vehicles ?? {}).map(([key, vehicle]) =>
			entry(state, key, vehicle.name, `vehicle.${key}.soc_pct`)
		)
	];
}
