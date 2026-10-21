import { cleanup, fireEvent, render, screen, within } from '@testing-library/svelte';
import type { ComponentProps } from 'svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { BoostCost } from '$lib/api/schemas';
import { drivingVehicle, system } from '$lib/testing/fixtures';
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
		onAdopt: vi.fn().mockResolvedValue(undefined),
		onAutomation: vi.fn().mockResolvedValue(undefined),
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

describe('Fahrverhalten', () => {
	const tesla = () => drivingVehicle('tesla');
	const egolf = () => drivingVehicle('egolf');

	function suggestion(): HTMLElement {
		return screen.getByRole('group', { name: 'Vorschlag' });
	}

	it('zeigt den Vorschlag mit Tag, Abfahrt, Ziel und Konfidenz', () => {
		show({ driving: tesla() });
		expect(
			within(suggestion()).getByText('Vorschlag: Mo 07:15 · 50 % (Konfidenz hoch)')
		).toBeVisible();
		cleanup();
		show({ driving: egolf() });
		expect(
			within(suggestion()).getByText('Vorschlag: Mo 08:00 · 50 % (Konfidenz niedrig)')
		).toBeVisible();
	});

	it('Übernehmen sendet Ziel, Abfahrt und Abfahrtstag des Vorschlags', async () => {
		const props = show({ driving: tesla() });
		await fireEvent.click(within(suggestion()).getByRole('button', { name: 'Übernehmen' }));
		expect(props.onAdopt).toHaveBeenCalledWith(50, '07:15', '2026-10-12');
	});

	it('Übernehmen sendet ohne Ziel nur Abfahrt und Abfahrtstag', async () => {
		const car = tesla();
		const props = show({
			driving: {
				...car,
				next_trip: { ...car.next_trip!, target_soc_pct: null, target_confidence: null }
			}
		});
		expect(within(suggestion()).getByText('Vorschlag: Mo 07:15 (Konfidenz hoch)')).toBeVisible();
		await fireEvent.click(within(suggestion()).getByRole('button', { name: 'Übernehmen' }));
		expect(props.onAdopt).toHaveBeenCalledWith(null, '07:15', '2026-10-12');
	});

	it('zeigt ohne Vorschlag „Kein Vorschlag (zu wenig Daten).“ und keine Taste', () => {
		show({ driving: { ...tesla(), next_trip: null } });
		expect(within(suggestion()).getByText('Kein Vorschlag (zu wenig Daten).')).toBeVisible();
		expect(within(suggestion()).queryByRole('button')).toBeNull();
	});

	it('zeigt Lesenden den Vorschlag ohne Taste', () => {
		show({ driving: tesla(), operate: false });
		expect(screen.getByText('Vorschlag: Mo 07:15 · 50 % (Konfidenz hoch)')).toBeVisible();
		expect(screen.queryByRole('button', { name: 'Übernehmen' })).toBeNull();
	});

	it('zeigt ohne Antwort zum Fahrverhalten weder Vorschlag noch Schalter', () => {
		show();
		expect(screen.queryByText(/Vorschlag/)).toBeNull();
		expect(screen.queryByRole('switch')).toBeNull();
	});

	it('Schalter der Automatik nur ab Bedienen', async () => {
		const props = show({ driving: tesla() });
		const toggle = screen.getByRole('switch', { name: 'Ziel und Abfahrt automatisch' });
		expect(toggle).toBeChecked();
		expect(toggle).toBeEnabled();
		await fireEvent.click(toggle);
		expect(props.onAutomation).toHaveBeenCalledWith(false);
	});

	it('schaltet die Automatik an, wenn sie aus ist', async () => {
		const props = show({ driving: egolf() });
		const toggle = screen.getByRole('switch', { name: 'Ziel und Abfahrt automatisch' });
		expect(toggle).not.toBeChecked();
		await fireEvent.click(toggle);
		expect(props.onAutomation).toHaveBeenCalledWith(true);
	});

	it('zeigt Lesenden den Schalter gesperrt', () => {
		show({ driving: tesla(), operate: false });
		const toggle = screen.getByRole('switch', { name: 'Ziel und Abfahrt automatisch' });
		expect(toggle).toBeChecked();
		expect(toggle).toBeDisabled();
	});

	it('sperrt den Schalter während einer Bedienung', () => {
		show({ driving: tesla(), busy: true });
		expect(screen.getByRole('switch', { name: 'Ziel und Abfahrt automatisch' })).toBeDisabled();
	});

	it('zeigt den Zustand der Automatik wörtlich vom Server', () => {
		show({ driving: tesla() });
		expect(
			screen.getByText(
				'Automatik pausiert bis Fr 17:30, weil Ziel oder Abfahrt von Hand geändert wurden.'
			)
		).toBeVisible();
		cleanup();
		show({ driving: egolf() });
		expect(screen.getByText('Automatik aus.')).toBeVisible();
	});

	it('beschreibt den Schalter mit dem Zustand der Automatik', () => {
		show({ driving: egolf() });
		expect(
			screen.getByRole('switch', { name: 'Ziel und Abfahrt automatisch' })
		).toHaveAccessibleDescription('Automatik aus.');
	});

	it('hält die Felder von Hand getrennt vom Vorschlag', async () => {
		const props = show({ mode: 'planned', planned: PLANNED, driving: tesla() });
		const manual = screen.getByRole('group', { name: 'Von Hand' });
		await fireEvent.input(within(manual).getByRole('spinbutton', { name: 'Ziel-SOC' }), {
			target: { value: '80' }
		});
		await fireEvent.click(within(manual).getByRole('button', { name: 'Übernehmen' }));
		expect(props.onTarget).toHaveBeenCalledWith(80, null);
		expect(props.onAdopt).not.toHaveBeenCalled();
	});
});

describe('Abfahrt an einem Tag (Ruling F-3)', () => {
	// Freitag, 09.10.2026, 20:00 in Wien
	beforeEach(() => {
		vi.useFakeTimers({ toFake: ['Date'] });
		vi.setSystemTime(new Date('2026-10-09T18:00:00Z'));
	});
	afterEach(() => vi.useRealTimers());

	const goal = { target_soc_pct: 60, departure: '07:15', departure_on: '2026-10-12' };

	it('nennt neben dem Feld den Tag der Abfahrt', () => {
		show({ mode: 'planned', planned: PLANNED, goal });
		const manual = screen.getByRole('group', { name: 'Von Hand' });
		expect(within(manual).getByText('am Mo 12.10.')).toBeVisible();
		expect(within(manual).getByLabelText('Abfahrt')).toHaveAccessibleDescription('am Mo 12.10.');
	});

	it('nennt keinen Tag für heute und für eine tägliche Abfahrt', () => {
		show({ mode: 'planned', planned: PLANNED, goal: { ...goal, departure_on: '2026-10-09' } });
		expect(screen.queryByText(/^am /)).toBeNull();
		cleanup();
		show({ mode: 'planned', planned: PLANNED, goal: { ...goal, departure_on: null } });
		expect(screen.queryByText(/^am /)).toBeNull();
	});

	it('nennt keinen Tag, der schon vorbei ist', () => {
		show({ mode: 'planned', planned: PLANNED, goal: { ...goal, departure_on: '2026-10-08' } });
		expect(screen.queryByText(/^am /)).toBeNull();
	});

	it('nimmt den Tag weg, sobald die Uhrzeit von Hand geändert wird', async () => {
		show({ mode: 'planned', planned: PLANNED, goal });
		await fireEvent.input(screen.getByLabelText('Abfahrt'), { target: { value: '08:00' } });
		expect(screen.queryByText('am Mo 12.10.')).toBeNull();
	});
});
