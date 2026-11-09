import { expect, it } from 'vitest';
import type { Palette } from '$lib/charts/palette';
import { heatDemand } from '$lib/testing/heating';
import { heatDemandOption, heatingCsv, heatingFilename } from './view';

const palette: Palette = {
	text: '#000',
	muted: '#555',
	grid: '#ccc',
	importPrice: '#00f',
	feedIn: '#0a0',
	pv: '#da0',
	load: '#80f',
	series: ['#111', '#222', '#333', '#444', '#555', '#666', '#777', '#888']
};

type Series = { name: string; type: string; data: unknown[] };
const seriesOf = (option: object) => (option as { series: Series[] }).series;

it('hat zwei Punktreihen (verwendet, nicht verwendet) und die Gerade der Anpassung', () => {
	const series = seriesOf(heatDemandOption(heatDemand(), palette));
	expect(series.map((s) => [s.name, s.type])).toEqual([
		['verwendet', 'scatter'],
		['nicht verwendet', 'scatter'],
		['Gerade', 'line']
	]);
	const xy = (s: Series) => s.data.map((d) => (d as { value: number[] }).value);
	expect(xy(series[0])).toEqual([
		[4.5, 70],
		[6, 88],
		[8, 103]
	]);
	expect(xy(series[1])).toEqual([
		[7, 96.5],
		[0.5, 41]
	]);
	// von 0 bis zum größten K·d (8): 32,5 + 8,5 · 8 = 100,5
	expect(series[2].data).toEqual([
		[0, 32.5],
		[8, 100.5]
	]);
});

it('lässt ohne Anpassung die Gerade weg und Punkte ohne Wert', () => {
	const demand = heatDemand({ fit: null, reason: 'Zu wenige Tage' });
	demand.points[0] = { ...demand.points[0], demand_kwh: null };
	const series = seriesOf(heatDemandOption(demand, palette));
	expect(series.map((s) => s.name)).toEqual(['verwendet', 'nicht verwendet']);
	expect(series[0].data).toHaveLength(2);
});

it('nennt die Datei nach der Saison', () => {
	expect(heatingFilename(heatDemand())).toBe('waermebedarf-2026-27.csv');
});

it('schreibt die CSV mit Kopf, Dezimalkomma und Semikolon', () => {
	const lines = heatingCsv(heatDemand()).split('\r\n');
	expect(lines[0]).toBe(
		'﻿Tag;Brennerstunden;Wärme Öl kWh;Öl l;Klima Strom heizen kWh;Klima Wärme kWh (geschätzt);Wärmebedarf kWh;Heizgradtage K·d;Außentemperatur °C;vollständig;in Auswertung;Hinweis'
	);
	expect(lines).toHaveLength(6);
	expect(lines[1]).toBe('20.10.2026;3,33;67,00;19,14;1,50;4,50;70,00;4,50;9,2;ja;ja;');
	expect(lines[4]).toBe(
		'23.10.2026;4,60;93,50;26,71;1,50;4,50;96,50;7,00;6,1;nein;nein;"Brennerstatus lückenhaft; Text mit ; und ""Anführungszeichen"""'
	);
});

it('lässt unbekannte Werte in der CSV leer (nie 0)', () => {
	const demand = heatDemand();
	demand.points = [
		{
			...demand.points[0],
			hdd_kd: null,
			outdoor_mean_c: null,
			burner_h: null,
			oil_heat_kwh: null,
			oil_l: null,
			ac_heat_el_kwh: null,
			ac_heat_kwh: null,
			demand_kwh: null
		}
	];
	expect(heatingCsv(demand).split('\r\n')[1]).toBe('20.10.2026;;;;;;;;;ja;ja;');
});
