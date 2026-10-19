import { fireEvent, render, screen } from '@testing-library/svelte';
import type { ComponentProps } from 'svelte';
import { expect, it, vi } from 'vitest';
import type { BoostCost } from '$lib/api/schemas';
import { system } from '$lib/testing/fixtures';
import VehicleCard from './VehicleCard.svelte';

type Props = ComponentProps<typeof VehicleCard>;

const COST: BoostCost = {
	applies: true,
	month_peak_kw: 5,
	vehicles: [{ id: 'tesla', extra_kw: 11.04, cost_eur: 52.99 }]
};
const SENTENCE = 'Boost kann bis zu 52,99 € Leistungspreis im Monat kosten (11,0 kW mehr Spitze).';

function show(changes: Partial<Props> = {}) {
	const vehicle = system().core.health!.vehicles.find((item) => item.id === 'tesla')!;
	const props: Props = {
		vehicle,
		wallbox: 'Garage',
		mode: 'min_pv',
		boost: { active: false, until: null, pending: false },
		operate: true,
		busy: false,
		notice: null,
		onMode: vi.fn().mockResolvedValue(undefined),
		onBoost: vi.fn().mockResolvedValue(undefined),
		goal: { target_soc_pct: 60, departure: '17:30' },
		planned: null,
		onTarget: vi.fn().mockResolvedValue(undefined),
		...changes
	};
	render(VehicleCard, props);
	return props;
}

it('zeigt die Mehrkosten des Boosts Bedienenden, wenn ein Leistungspreis gilt', () => {
	show({ boostCost: COST });
	expect(screen.getByText(SENTENCE)).toBeVisible();
});

it('zeigt die Mehrkosten nicht ohne applies', () => {
	show({ boostCost: { applies: false, month_peak_kw: null, vehicles: [] } });
	expect(screen.queryByText(/Leistungspreis im Monat/)).not.toBeInTheDocument();
});

it('zeigt die Mehrkosten nicht ohne Antwort', () => {
	show();
	expect(screen.queryByText(/Leistungspreis im Monat/)).not.toBeInTheDocument();
});

it('zeigt die Mehrkosten nicht Lesenden', () => {
	show({ boostCost: COST, operate: false });
	expect(screen.queryByText(/Leistungspreis im Monat/)).not.toBeInTheDocument();
});

it('zeigt die Zeile zur Session unter den Fakten', () => {
	show({ sessionLine: 'Letzte Session 08.10. 18:00–21:30 · 12,4 kWh' });
	expect(screen.getByText('Letzte Session 08.10. 18:00–21:30 · 12,4 kWh')).toBeVisible();
});

it('zeigt ohne Session keine Zeile', () => {
	show();
	expect(screen.queryByText(/Session/)).not.toBeInTheDocument();
});

const NB = '\u00a0';
const PLANNED = {
	text: `Fertig um 16:30 · Ziel 60${NB}% bis 17:30`,
	notes: ['Ladestand geschätzt']
};

it('zeigt in „Geplant“ die Zeile und ihre Zusätze, auch Lesenden', () => {
	show({ mode: 'planned', planned: PLANNED, operate: false });
	// testing-library vergleicht den Text mit Leerzeichen statt geschützter Leerzeichen
	expect(screen.getByText('Fertig um 16:30 · Ziel 60 % bis 17:30')).toBeVisible();
	expect(screen.getByText('Ladestand geschätzt')).toBeVisible();
	expect(screen.queryByRole('spinbutton', { name: 'Ziel-SOC' })).toBeNull();
});

it('bietet in „Geplant“ Ziel-SOC, Abfahrt und „Übernehmen“', () => {
	show({ mode: 'planned', planned: PLANNED });
	expect(screen.getByRole('spinbutton', { name: 'Ziel-SOC' })).toHaveValue(60);
	expect(screen.getByLabelText('Abfahrt')).toHaveValue('17:30');
	expect(screen.getByRole('button', { name: 'Übernehmen' })).toBeEnabled();
});

it('zeigt die Felder nur in „Geplant“', () => {
	show({ mode: 'pv' });
	expect(screen.queryByRole('spinbutton', { name: 'Ziel-SOC' })).toBeNull();
	expect(screen.queryByRole('button', { name: 'Übernehmen' })).toBeNull();
});

it('schickt nur die geänderten Felder', async () => {
	const props = show({ mode: 'planned', planned: PLANNED });
	await fireEvent.input(screen.getByRole('spinbutton', { name: 'Ziel-SOC' }), {
		target: { value: '80' }
	});
	await fireEvent.click(screen.getByRole('button', { name: 'Übernehmen' }));
	expect(props.onTarget).toHaveBeenCalledWith(80, null);
	await fireEvent.input(screen.getByLabelText('Abfahrt'), { target: { value: '07:00' } });
	await fireEvent.click(screen.getByRole('button', { name: 'Übernehmen' }));
	expect(props.onTarget).toHaveBeenLastCalledWith(null, '07:00');
});

it('schickt nichts ohne Änderung', async () => {
	const props = show({ mode: 'planned', planned: PLANNED });
	await fireEvent.click(screen.getByRole('button', { name: 'Übernehmen' }));
	expect(props.onTarget).not.toHaveBeenCalled();
});

it('meldet ein ungültiges Ziel mit dem Satz der app und schickt nichts', async () => {
	const props = show({ mode: 'planned', planned: PLANNED });
	await fireEvent.input(screen.getByRole('spinbutton', { name: 'Ziel-SOC' }), {
		target: { value: '101' }
	});
	await fireEvent.click(screen.getByRole('button', { name: 'Übernehmen' }));
	expect(screen.getByText('Ziel-SOC „101“ liegt außerhalb von 20–100 %.')).toBeVisible();
	expect(props.onTarget).not.toHaveBeenCalled();
});

it('nennt einen geschätzten Ladestand aus Health', () => {
	const vehicle = {
		...system().core.health!.vehicles.find((item) => item.id === 'tesla')!,
		soc_pct: 48.9,
		soc_source: 'estimated' as const
	};
	show({ vehicle, soc: { id: 'tesla', label: 'Tesla Model 3', pct: 70, quality: 'stale' } });
	expect(screen.getByText('geschätzt').parentElement).toHaveTextContent('49 % geschätzt');
	expect(screen.queryByText('veraltet')).toBeNull();
});
