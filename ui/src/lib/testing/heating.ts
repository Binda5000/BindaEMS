// Testdaten der Heizung (nur für Tests): Statistik-Block `heating` und Antwort von /api/heating/demand
import * as v from 'valibot';
import {
	HeatDemandSchema,
	HeatingStatsSchema,
	type HeatDemand,
	type HeatingStats
} from '$lib/api/schemas';

export function heatingStats(overrides: Partial<HeatingStats> = {}): HeatingStats {
	return v.parse(HeatingStatsSchema, {
		burner_h: 120.5,
		oil_heat_kwh: 2530.5,
		oil_l: 722.5,
		oil_eur: 794.75,
		ac_heat_el_kwh: 80.25,
		ac_heat_kwh: 240.75,
		demand_kwh: 2771.25,
		hdd_kd: 310.4,
		days: 31,
		complete_days: 30,
		first_day: '2026-10-01',
		settings_missing: false,
		ac_power_known: true,
		...overrides
	});
}

const DAYS = [
	['2026-10-20', 4.5, 9.2, 70.0, true, true],
	['2026-10-21', 6.0, 7.5, 88.0, true, true],
	['2026-10-22', 8.0, 5.0, 103.0, true, true],
	['2026-10-23', 7.0, 6.1, 96.5, false, false],
	['2026-10-24', 0.5, 14.5, 41.0, true, false]
] as const;

export function heatDemand(overrides: Partial<HeatDemand> = {}): HeatDemand {
	return v.parse(HeatDemandSchema, {
		season: '2026/27',
		first: '2026-07-01',
		last: '2027-06-30',
		points: DAYS.map(([day, hdd, mean, demand, complete, used]) => ({
			day,
			hdd_kd: hdd,
			outdoor_mean_c: mean,
			burner_h: demand / 21,
			oil_heat_kwh: demand - 3,
			oil_l: (demand - 3) / 3.5,
			ac_heat_el_kwh: 1.5,
			ac_heat_kwh: 4.5,
			demand_kwh: demand,
			complete,
			used,
			note: complete ? null : 'Brennerstatus lückenhaft; Text mit ; und "Anführungszeichen"'
		})),
		fit: { per_kd: 8.5, base_kwh_day: 32.5, r2: 0.912, n: 3, through_origin: false },
		reason: null,
		totals: {
			oil_heat_kwh: 2530.5,
			oil_l: 722.5,
			oil_eur: 794.75,
			ac_heat_el_kwh: 80.25,
			demand_kwh: 2771.25,
			hdd_kd: 310.4
		},
		ac_by_month: { '2026-10': 12.5, '2026-11': 40.25 },
		heating_limit_c: 15,
		ac_cop: 3,
		ac_power_known: true,
		...overrides
	});
}
