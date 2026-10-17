import { expect, it } from 'vitest';
import type { BoostCost } from '$lib/api/schemas';
import { boostCostText } from './boostCost';

const cost: BoostCost = {
	applies: true,
	month_peak_kw: 5,
	vehicles: [
		{ id: 'tesla', extra_kw: 11.04, cost_eur: 52.99 },
		{ id: 'egolf', extra_kw: 7.36, cost_eur: 9.5 }
	]
};

it('nennt Euro mit zwei und kW mit einer Nachkommastelle', () => {
	expect(boostCostText(cost, 'tesla')).toBe(
		'Boost kann bis zu 52,99 € Leistungspreis im Monat kosten (11,0 kW mehr Spitze).'
	);
	expect(boostCostText(cost, 'egolf')).toBe(
		'Boost kann bis zu 9,50 € Leistungspreis im Monat kosten (7,4 kW mehr Spitze).'
	);
});

it('schweigt ohne Satz, ohne Antwort und für unbekannte Fahrzeuge', () => {
	expect(boostCostText({ applies: false, month_peak_kw: null, vehicles: [] }, 'tesla')).toBeNull();
	expect(boostCostText({ ...cost, applies: false }, 'tesla')).toBeNull();
	expect(boostCostText(undefined, 'tesla')).toBeNull();
	expect(boostCostText(null, 'tesla')).toBeNull();
	expect(boostCostText(cost, 'unbekannt')).toBeNull();
});
