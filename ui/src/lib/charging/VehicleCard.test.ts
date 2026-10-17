import { render, screen } from '@testing-library/svelte';
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
		...changes
	};
	render(VehicleCard, props);
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

it('zeigt die Mehrkosten nicht Betrachtenden', () => {
	show({ boostCost: COST, operate: false });
	expect(screen.queryByText(/Leistungspreis im Monat/)).not.toBeInTheDocument();
});
