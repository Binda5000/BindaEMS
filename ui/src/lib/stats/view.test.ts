import { describe, expect, it } from 'vitest';
import { formatEur, formatRatio } from '$lib/format';
import { stats } from '$lib/testing/fixtures';
import type { Stats } from '$lib/api/schemas';
import { bucketLabel, bucketLabels, costRows, hints, savingsRows, tiles } from './view';

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
