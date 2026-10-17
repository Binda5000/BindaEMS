import { describe, expect, it } from 'vitest';
import { formatEnergy, formatEur, formatPower, formatRatio } from '$lib/format';
import { chargingSession, stats } from '$lib/testing/fixtures';
import type { Stats } from '$lib/api/schemas';
import {
	bucketLabel,
	bucketLabels,
	checkNotice,
	checkRows,
	consumerRows,
	costRows,
	hints,
	peakRows,
	savingsRows,
	sessionRows,
	tiles,
	vehicleLabel,
	vehicleRows
} from './view';

function withMoney(money: Partial<Stats['money']>, rest: Partial<Stats> = {}): Stats {
	const base = stats();
	return { ...base, ...rest, money: { ...base.money, ...money } };
}

describe('Kacheln', () => {
	it('shows the five tiles in order', () => {
		const result = tiles(stats());
		expect(result.map((tile) => tile.label)).toEqual([
			'Autarkie',
			'Eigenverbrauch',
			'Kosten',
			'Ersparnis',
			'Einspeiseerlös'
		]);
		expect(result.map((tile) => tile.value)).toEqual([
			formatRatio(0.758),
			formatRatio(0.919),
			formatEur(41.605),
			formatEur(60.129),
			formatEur(1.065)
		]);
		expect(result.find((tile) => tile.label === 'Ersparnis')?.subtitle).toBe(
			'gegenüber Fixtarif ohne Anlage'
		);
		expect(result.filter((tile) => tile.subtitle !== undefined)).toHaveLength(1);
	});

	it('shows unknown values as –', () => {
		const base = stats();
		const result = tiles({
			...base,
			ratios: { autarky: null, self_consumption: null },
			money: {
				...base.money,
				total_eur: null,
				savings_no_plant_eur: null,
				revenue_eur: null
			}
		});
		expect(result.map((tile) => tile.value)).toEqual(['–', '–', '–', '–', '–']);
	});
});

describe('Karte Kosten', () => {
	it('zeigt Bezug bis Summe, im Monat mit Leistungspreis', () => {
		const rows = costRows(stats());
		expect(rows.map((row) => row.label)).toEqual([
			'Bezug',
			'Einspeiseerlös',
			'Netto',
			'Grundgebühren',
			'Leistungspreis',
			'Summe'
		]);
		expect(rows.map((row) => row.value)).toEqual([
			formatEur(8.003),
			formatEur(1.065),
			formatEur(6.938),
			formatEur(1.672),
			formatEur(32.995),
			formatEur(41.605)
		]);
		expect(rows.at(-1)?.strong).toBe(true);
	});

	it('shows power price only for month and year', () => {
		const labels = (stats: Stats) => costRows(stats).map((row) => row.label);
		expect(labels(withMoney({ power_eur: 12 }, { period: 'year' }))).toContain('Leistungspreis');
		expect(labels(withMoney({ power_eur: 12 }, { period: 'month' }))).toContain('Leistungspreis');
		// Tag und Woche kennen keinen Leistungspreis, auch wenn ein Wert mitkäme
		expect(labels(withMoney({ power_eur: 12 }, { period: 'week' }))).not.toContain(
			'Leistungspreis'
		);
		expect(labels(withMoney({ power_eur: 12 }, { period: 'day' }))).not.toContain('Leistungspreis');
		// im Monat ohne Leistungspreis im Tarif entfällt die Zeile
		expect(labels(withMoney({ power_eur: null }, { period: 'month' }))).not.toContain(
			'Leistungspreis'
		);
	});

	it('zeigt fehlende Beträge als – und rechnet nichts nach', () => {
		const rows = costRows(
			withMoney({ import_eur: null, net_eur: null, total_eur: null, revenue_eur: 1 })
		);
		const value = (label: string) => rows.find((row) => row.label === label)?.value;
		expect([value('Bezug'), value('Netto'), value('Summe')]).toEqual(['–', '–', '–']);
		expect(value('Einspeiseerlös')).toBe(formatEur(1));
	});

	it('nennt die beiden Ersparnisse', () => {
		const rows = savingsRows(stats());
		expect(rows.map((row) => [row.label, row.value])).toEqual([
			['Ersparnis ohne Anlage', formatEur(60.129)],
			['Ersparnis bei gleichem Netzbezug', formatEur(8.203)]
		]);
		expect(
			savingsRows(withMoney({ savings_no_plant_eur: null, savings_same_import_eur: null })).map(
				(row) => row.value
			)
		).toEqual(['–', '–']);
	});
});

describe('Hinweise im Kopf', () => {
	it('nennt Abdeckung, Schätzung und den laufenden Zeitraum', () => {
		expect(hints(stats({ coverage: 0.953, estimated_slots: 2 }), true)).toEqual([
			'Abdeckung 95 %',
			'2 Viertelstunden geschätzt',
			'bis jetzt'
		]);
	});

	it('lässt Schätzung und „bis jetzt“ weg, wenn es nichts zu sagen gibt', () => {
		expect(hints(stats({ coverage: 1, estimated_slots: 0 }), false)).toEqual(['Abdeckung 100 %']);
		expect(hints(stats({ estimated_slots: 1 }), false)).toContain('1 Viertelstunde geschätzt');
	});
});

describe('Beschriftung der Balken', () => {
	it('beschriftet Stunden in Wiener Zeit, Tage und Monate', () => {
		expect(bucketLabel('day', '2026-10-08T00:00:00+02:00')).toBe('00:00');
		expect(bucketLabel('day', '2026-10-08T13:00:00+02:00')).toBe('13:00');
		expect(bucketLabel('week', '2026-10-09')).toBe('Fr., 09.10.');
		expect(bucketLabel('month', '2026-10-09')).toBe('09.10.');
		expect(bucketLabel('year', '2026-10')).toBe('Okt.');
		expect(bucketLabel('year', '2026-01')).toBe('Jän.');
	});
});

describe('Beschriftung der Stundenbalken an Tagen mit Zeitumstellung', () => {
	it('unterscheidet die doppelte Stunde im Herbst nach MESZ und MEZ', () => {
		const starts = [
			'2026-10-25T00:00:00+02:00',
			'2026-10-25T01:00:00+02:00',
			'2026-10-25T02:00:00+02:00',
			'2026-10-25T02:00:00+01:00',
			'2026-10-25T03:00:00+01:00'
		];
		expect(bucketLabels('day', starts)).toEqual([
			'00:00',
			'01:00',
			'02:00 MESZ',
			'02:00 MEZ',
			'03:00'
		]);
	});

	it('liest die Zone aus dem Zeitpunkt, nicht aus dem Versatz im Text', () => {
		// dieselben Zeitpunkte in UTC geschrieben
		const starts = ['2026-10-25T00:00:00Z', '2026-10-25T01:00:00Z'];
		expect(bucketLabels('day', starts)).toEqual(['02:00 MESZ', '02:00 MEZ']);
	});

	it('lässt ohne doppelte Stunde alles beim Alten', () => {
		const starts = ['2026-10-08T00:00:00+02:00', '2026-10-08T01:00:00+02:00'];
		expect(bucketLabels('day', starts)).toEqual(['00:00', '01:00']);
		expect(bucketLabels('month', ['2026-10-01', '2026-10-02'])).toEqual(['01.10.', '02.10.']);
	});
});

const vehicle = (overrides: Partial<Stats['vehicles'][number]> = {}) => ({
	...stats().vehicles[0],
	...overrides
});

describe('Karte Autos', () => {
	it('lists vehicles with PV, battery and grid share and the battery-to-cars row', () => {
		const { rows, batteryToVehicles } = vehicleRows(stats());
		expect(rows.map((row) => row.name)).toEqual(['Tesla Model 3', 'Gast']);
		expect(rows[0]).toMatchObject({
			energy: formatEnergy(20.01),
			pv: formatEnergy(11.729),
			battery: formatEnergy(6.901),
			grid: formatEnergy(1.38),
			cost: formatEur(0.974),
			sessions: '2'
		});
		expect(batteryToVehicles).toBe(formatEnergy(12.901));
	});

	it('rechnet ct/kWh als Kosten durch Energie mal 100 und zeigt ohne Kosten einen Strich', () => {
		const rows = (list: Stats['vehicles']) => vehicleRows(stats({ vehicles: list })).rows;
		expect(rows([vehicle({ kwh: 20, cost_eur: 1 })])[0].ctPerKwh).toBe('5,00');
		expect(rows([vehicle({ kwh: 20, cost_eur: 0 })])[0].ctPerKwh).toBe('0,00');
		expect(rows([vehicle({ kwh: 20, cost_eur: null })])[0].ctPerKwh).toBe('–');
		expect(rows([vehicle({ kwh: 0, cost_eur: 1 })])[0].ctPerKwh).toBe('–');
		expect(rows([vehicle({ cost_eur: null })])[0].cost).toBe('–');
	});

	it('zeigt ohne Autos keine Akku-Zeile', () => {
		expect(vehicleRows(stats({ vehicles: [], battery_to_vehicles_kwh: 0 }))).toEqual({
			rows: [],
			batteryToVehicles: null
		});
	});

	it('benennt Gast und Unbekannt deutsch und zeigt einen fremden Schlüssel an', () => {
		expect(vehicleLabel('guest', 'guest')).toBe('Gast');
		expect(vehicleLabel('unknown', '')).toBe('Unbekannt');
		expect(vehicleLabel('tesla', 'Tesla Model 3')).toBe('Tesla Model 3');
		expect(vehicleLabel('altauto', '')).toBe('altauto');
	});
});

describe('Karte Ladesessions', () => {
	it('marks an open session as läuft', () => {
		const rows = sessionRows([
			chargingSession({ open: true, end: null }),
			chargingSession({
				id: 9,
				open: false,
				start: '2026-10-05T08:00:00+00:00',
				end: '2026-10-05T12:30:00+00:00'
			})
		]);
		expect(rows[0].end).toBe('läuft');
		expect(rows[1].end).toBe('14:30');
	});

	it('zeigt Beginn, Wallbox, Auto, Energie, Aufteilung und Kosten', () => {
		const [row] = sessionRows([
			chargingSession({
				start: '2026-10-05T08:00:00+00:00',
				end: '2026-10-06T08:00:00+00:00',
				wallbox_name: 'Garage',
				vehicle: 'tesla',
				vehicle_name: 'Tesla Model 3',
				kwh: 18.63,
				pv_kwh: 11.729,
				battery_kwh: 6.901,
				grid_kwh: 0,
				cost_eur: 0.458
			})
		]);
		expect(row.start).toBe('05.10.2026, 10:00');
		expect(row.end).toBe('06.10.2026, 10:00');
		expect(row.wallbox).toBe('Garage');
		expect(row.vehicle).toBe('Tesla Model 3');
		expect(row.energy).toBe(formatEnergy(18.63));
		expect(row.split).toBe('11,7 / 6,9 / 0,0\u00a0kWh');
		expect(row.cost).toBe(formatEur(0.458));
	});

	it('zeigt Kosten ohne Preis als Strich und benennt Gast und Unbekannt', () => {
		const rows = sessionRows([
			chargingSession({ cost_eur: null, vehicle: 'guest', vehicle_name: 'Gast' }),
			chargingSession({ vehicle: 'unknown', vehicle_name: 'unknown' })
		]);
		expect(rows[0].cost).toBe('–');
		expect(rows.map((row) => row.vehicle)).toEqual(['Gast', 'Unbekannt']);
	});
});

describe('Karte Verbraucher', () => {
	it('shows the consumer reason as title when kWh is missing', () => {
		const rows = consumerRows(
			stats({
				consumers: [
					{ id: 1, name: 'Küche', kwh: 3.456, note: null },
					{ id: 2, name: 'Büro', kwh: null, note: 'Tag nicht gespeichert' }
				]
			})
		);
		expect(rows).toEqual([
			{ id: 1, name: 'Küche', value: formatEnergy(3.456), title: undefined },
			{ id: 2, name: 'Büro', value: '–', title: 'Tag nicht gespeichert' }
		]);
	});
});

describe('Karte Monatsspitzen', () => {
	it('zeigt Monat, Spitze, Zeitpunkt und Leistungspreis', () => {
		const rows = peakRows(
			stats({
				month_peaks: [
					{
						month: '2026-10',
						kw: 5.499,
						slot_start: '2026-10-04T02:45:00+00:00',
						power_eur: 32.995
					},
					{ month: '2026-09', kw: 4.2, slot_start: '2026-09-04T12:00:00+00:00', power_eur: null }
				]
			})
		);
		expect(rows).toEqual([
			{
				month: 'Oktober 2026',
				peak: formatPower(5499),
				at: '04.10.2026, 04:45',
				price: formatEur(32.995)
			},
			{ month: 'September 2026', peak: formatPower(4200), at: '04.09.2026, 14:00', price: '–' }
		]);
	});
});

describe('Karte Abgleich', () => {
	it('benennt die Größen und zeigt Abweichung mit einer Nachkommastelle', () => {
		const rows = checkRows(stats(), { evcs: 'Carport' });
		expect(rows.map((row) => row.label)).toEqual([
			'Netzbezug',
			'Einspeisung',
			'PV (ohne Markierung)',
			'Wallbox Carport',
			'Wallbox twc'
		]);
		expect(rows[0]).toMatchObject({
			ledger: formatEnergy(52.358),
			counter: formatEnergy(52.881),
			deviation: '-1,0\u00a0%'
		});
	});

	it('zeigt einen fehlenden Zähler als Strich', () => {
		const base = stats();
		const [row] = checkRows(
			stats({
				checks: {
					...base.checks,
					totals: [{ signal: 'import', ledger_kwh: 1, counter_kwh: null, deviation_pct: null }]
				}
			}),
			{}
		);
		expect([row.counter, row.deviation]).toEqual(['–', '–']);
	});

	it('names flagged days in the checks card', () => {
		const notice = (days: number, flagged: string[]) =>
			checkNotice(stats({ checks: { days, flagged_days: flagged, totals: [] } }));
		expect(notice(7, ['2026-10-12'])).toBe('Mehr als 1 % Abweichung am 12.10.');
		expect(notice(7, ['2026-10-12', '2026-10-15'])).toBe(
			'Mehr als 1 % Abweichung am 12.10. und 15.10.'
		);
		expect(notice(7, ['2026-10-02', '2026-10-12', '2026-10-15'])).toBe(
			'Mehr als 1 % Abweichung am 02.10., 12.10. und 15.10.'
		);
		expect(notice(7, [])).toBeNull();
		expect(notice(0, [])).toBe('Noch kein Tag abgeglichen.');
	});
});
