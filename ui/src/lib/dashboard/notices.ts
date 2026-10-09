// Hinweise der Übersicht: Alarme des core, gestörte Komponenten und Warnungen der Einstellungen
import type { SystemResponse } from '$lib/api/schemas';

export interface Notice {
	level: 'error' | 'warning' | 'info';
	text: string;
}

const RANK: Record<Notice['level'], number> = { error: 0, warning: 1, info: 2 };

export function noticesFrom(system: SystemResponse | undefined): Notice[] {
	if (!system) return [];
	const all: Notice[] = [
		...(system.core.health?.alarms ?? []).map((alarm) => ({
			level:
				alarm.severity === 'error'
					? ('error' as const)
					: alarm.severity === 'warning'
						? ('warning' as const)
						: ('info' as const),
			text: alarm.message
		})),
		...system.components
			.filter((component) => !component.ok)
			.map((component) => ({ level: 'warning' as const, text: component.message })),
		...system.warnings.map((text) => ({ level: 'warning' as const, text }))
	];
	const seen = new Set<string>();
	return all
		.filter((notice) => !seen.has(notice.text) && seen.add(notice.text))
		.sort((a, b) => RANK[a.level] - RANK[b.level]); // stabil: Reihenfolge innerhalb bleibt
}
