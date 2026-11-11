// Beispielantworten von /api/pv und /api/pv/period für die Tests der Seite „PV-Anlagen“:
// Anlage „Huawei“ (core, 9,8 kWp) mit den Strings „Süd“ (4,1 kWp) und „West“ (5,7 kWp) aus HA
import type { PvNode, PvPeriod, PvResponse, PvSource } from '$lib/api/schemas';

export const HUAWEI: PvSource = {
	id: 1,
	name: 'Huawei',
	parent_id: null,
	color: '#6e7781',
	source_kind: 'core',
	power_ref: 'pv.huawei.power_w',
	power_unit: null,
	kwp: 9.8,
	sort: 0
};

export const SUED: PvSource = {
	id: 2,
	name: 'Süd',
	parent_id: 1,
	color: '#d18f00',
	source_kind: 'ha',
	power_ref: 'sensor.pv1_leistung',
	power_unit: 'W',
	kwp: 4.1,
	sort: 0
};

export const WEST: PvSource = {
	id: 3,
	name: 'West',
	parent_id: 1,
	color: '#1f6feb',
	source_kind: 'ha',
	power_ref: 'sensor.pv2_leistung',
	power_unit: 'W',
	kwp: 5.7,
	sort: 1
};

export function pvNode(source: PvSource, changes: Partial<PvNode> = {}): PvNode {
	return {
		id: source.id,
		name: source.name,
		color: source.color,
		kind: source.parent_id === null ? 'system' : 'string',
		power_w: 1000,
		kwh_today: 3.6,
		kwp: source.kwp,
		kwh_per_kwp: null,
		note: null,
		children: [],
		strings_power_w: null,
		strings_kwh_today: null,
		...changes
	};
}

/** Antwort von GET /api/pv: die Anlage mit ihren beiden Strings, dazu „Nicht zugeordnet“ */
export function pvTree(sources: PvSource[] = [HUAWEI, SUED, WEST]): PvResponse {
	const systems = sources.filter((source) => source.parent_id === null);
	const tree: PvNode[] = systems.map((system) =>
		pvNode(system, {
			power_w: 2960,
			children: sources
				.filter((source) => source.parent_id === system.id)
				.map((source) => pvNode(source, { power_w: source.id === SUED.id ? 1840 : 1210 }))
		})
	);
	if (tree.length > 0) {
		tree.push({
			id: null,
			name: 'Nicht zugeordnet',
			color: '#9ca3af',
			kind: 'unassigned',
			power_w: 0,
			kwh_today: 0,
			kwp: null,
			kwh_per_kwp: null,
			note: null,
			children: [],
			strings_power_w: null,
			strings_kwh_today: null
		});
	}
	return { total_w: 2960, total_kwh: 3.6, tree, sources, energy_since: null };
}

type PeriodSource = PvPeriod['sources'][number];

function periodSource(id: number, kwh: (number | null)[], kwp: number | null): PeriodSource {
	const known = kwh.filter((value): value is number => value !== null);
	const sum = known.length > 0 ? Math.round(known.reduce((a, b) => a + b, 0) * 1000) / 1000 : null;
	return {
		id,
		kwh,
		sum_kwh: sum,
		kwh_per_kwp: sum !== null && kwp ? Math.round((sum / kwp) * 1000) / 1000 : null,
		first_known: null,
		note: sum === null ? 'Tag nicht gespeichert' : null
	};
}

const WEEK_DAYS = [
	'2026-11-02',
	'2026-11-03',
	'2026-11-04',
	'2026-11-05',
	'2026-11-06',
	'2026-11-07',
	'2026-11-08'
];

/** Antwort von GET /api/pv/period für die Woche 2.–8.11.2026: Süd 23,3 kWh, West 27,8 kWh */
export function pvWeek(): PvPeriod {
	return {
		period: 'week',
		first: '2026-11-02',
		last: '2026-11-08',
		buckets: WEEK_DAYS.map((day) => ({ start: day, end: day })),
		total: { kwh: [7, 8, 6, 7, 8, 7, 6.6], sum_kwh: 49.6 },
		unassigned: { kwh: [0, 0, 0, 0, 0, 0, 0], sum_kwh: 0 },
		sources: [
			periodSource(1, [7, 8, 6, 7, 8, 7, 6.6], 9.8),
			periodSource(2, [3, 4, 2, 3, 4, 4, 3.3], 4.1),
			periodSource(3, [4, 4, 3, 4, 5, 4, 3.8], 5.7)
		]
	};
}

const MONTHS = Array.from({ length: 12 }, (_, index) => {
	const month = String(index + 1).padStart(2, '0');
	const last = new Date(Date.UTC(2026, index + 1, 0)).getUTCDate();
	return { start: `2026-${month}-01`, end: `2026-${month}-${last}` };
});

/** Antwort von GET /api/pv/period für 2026: Werte erst ab 30.08., die Strings ab 21.09. */
export function pvYear(): PvPeriod {
	const none = (count: number) => Array.from({ length: count }, () => null);
	const huawei = periodSource(1, [...none(7), 12, 260, 180, 120, ...none(1)], 9.8);
	const sued = periodSource(2, [...none(8), 40, 80, 50, ...none(1)], 4.1);
	const west = periodSource(3, [...none(8), 45, 90, 55, ...none(1)], 5.7);
	return {
		period: 'year',
		first: '2026-01-01',
		last: '2026-12-31',
		buckets: MONTHS,
		total: { kwh: [...none(7), 12, 260, 180, 120, ...none(1)], sum_kwh: 572 },
		unassigned: { kwh: [...none(7), 0, 0, 0, 0, ...none(1)], sum_kwh: 0 },
		sources: [
			{ ...huawei, first_known: '2026-08-30' },
			{ ...sued, first_known: '2026-09-21' },
			{ ...west, first_known: '2026-09-21' }
		]
	};
}
