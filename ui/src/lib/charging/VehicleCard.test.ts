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
		onNowLimit: vi.fn().mockResolvedValue(undefined),
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

it('bietet in „Geplant“ Ziel-SOC, Abfahrt und „Ziel speichern“', () => {
	show({ mode: 'planned', planned: PLANNED });
	expect(screen.getByRole('spinbutton', { name: 'Ziel-SOC' })).toHaveValue(60);
	expect(screen.getByLabelText('Abfahrt')).toHaveValue('17:30');
	expect(screen.getByRole('button', { name: 'Ziel speichern' })).toBeEnabled();
});

it('zeigt die Felder nur in „Geplant“', () => {
	show({ mode: 'pv' });
	expect(screen.queryByRole('spinbutton', { name: 'Ziel-SOC' })).toBeNull();
	expect(screen.queryByRole('button', { name: 'Ziel speichern' })).toBeNull();
});

it('schickt nur die geänderten Felder', async () => {
	const props = show({ mode: 'planned', planned: PLANNED });
	await fireEvent.input(screen.getByRole('spinbutton', { name: 'Ziel-SOC' }), {
		target: { value: '80' }
	});
	await fireEvent.click(screen.getByRole('button', { name: 'Ziel speichern' }));
	expect(props.onTarget).toHaveBeenCalledWith(80, null);
	await fireEvent.input(screen.getByLabelText('Abfahrt'), { target: { value: '07:00' } });
	await fireEvent.click(screen.getByRole('button', { name: 'Ziel speichern' }));
	expect(props.onTarget).toHaveBeenLastCalledWith(null, '07:00');
});

it('schickt nichts ohne Änderung', async () => {
	const props = show({ mode: 'planned', planned: PLANNED });
	await fireEvent.click(screen.getByRole('button', { name: 'Ziel speichern' }));
	expect(props.onTarget).not.toHaveBeenCalled();
});

it('meldet ein ungültiges Ziel mit dem Satz der app und schickt nichts', async () => {
	const props = show({ mode: 'planned', planned: PLANNED });
	await fireEvent.input(screen.getByRole('spinbutton', { name: 'Ziel-SOC' }), {
		target: { value: '101' }
	});
	await fireEvent.click(screen.getByRole('button', { name: 'Ziel speichern' }));
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

it('sagt „geschätzt“ auf der Karte nur einmal', () => {
	const vehicle = {
		...system().core.health!.vehicles.find((item) => item.id === 'tesla')!,
		soc_pct: 48.9,
		soc_source: 'estimated' as const
	};
	show({ vehicle, mode: 'planned', planned: PLANNED });
	expect(screen.getAllByText(/geschätzt/)).toHaveLength(1);
	expect(screen.getByText('geschätzt').parentElement).toHaveTextContent('49 % geschätzt');
	// ohne Zahl im Feld bleibt der Zusatz von „Geplant“
	cleanup();
	show({ vehicle: { ...vehicle, soc_pct: null }, mode: 'planned', planned: PLANNED });
	expect(screen.getAllByText(/geschätzt/)).toHaveLength(1);
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

	it('„Vorschlag übernehmen“ sendet Ziel, Abfahrt und Abfahrtstag des Vorschlags', async () => {
		const props = show({ driving: tesla() });
		await fireEvent.click(
			within(suggestion()).getByRole('button', { name: 'Vorschlag übernehmen' })
		);
		expect(props.onAdopt).toHaveBeenCalledWith(50, '07:15', '2026-10-12');
	});

	it('„Vorschlag übernehmen“ sendet ohne Ziel nur Abfahrt und Abfahrtstag', async () => {
		const car = tesla();
		const props = show({
			driving: {
				...car,
				next_trip: { ...car.next_trip!, target_soc_pct: null, target_confidence: null }
			}
		});
		expect(within(suggestion()).getByText('Vorschlag: Mo 07:15 (Konfidenz hoch)')).toBeVisible();
		await fireEvent.click(
			within(suggestion()).getByRole('button', { name: 'Vorschlag übernehmen' })
		);
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
		expect(screen.queryByRole('button', { name: 'Vorschlag übernehmen' })).toBeNull();
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
		await fireEvent.click(within(manual).getByRole('button', { name: 'Ziel speichern' }));
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

describe('Höchststrom in „Sofort“', () => {
	const SETUP = { max_a: null, range: { min_a: 5, max_a: 16, phases: 3 }, voltage: 230 };

	function limitSelect(): HTMLElement {
		return screen.getByRole('combobox', { name: 'Höchststrom' });
	}

	it('zeigt die Auswahl in „Sofort“ mit „Volle Leistung“ vorne', () => {
		show({ mode: 'now', nowLimit: SETUP });
		expect(limitSelect()).toHaveValue('full');
		const options = within(limitSelect()).getAllByRole('option');
		expect(options).toHaveLength(12);
		expect(options[0]).toHaveTextContent('Volle Leistung (16 A · 11,0 kW)');
		expect(options.at(-1)).toHaveTextContent('5 A · 3,5 kW');
	});

	it('zeigt die Auswahl nur in „Sofort“', () => {
		show({ mode: 'pv', nowLimit: SETUP });
		expect(screen.queryByRole('combobox', { name: 'Höchststrom' })).toBeNull();
	});

	it('zeigt die Auswahl nur ab „Bedienen“', () => {
		show({ mode: 'now', nowLimit: SETUP, operate: false });
		expect(screen.queryByRole('combobox', { name: 'Höchststrom' })).toBeNull();
	});

	it('zeigt die Auswahl nicht ohne Grenzen des Fahrzeugs', () => {
		show({ mode: 'now', nowLimit: null });
		expect(screen.queryByRole('combobox', { name: 'Höchststrom' })).toBeNull();
	});

	it('wählt den Stand der Absicht vor', () => {
		show({ mode: 'now', nowLimit: { ...SETUP, max_a: 10 } });
		expect(limitSelect()).toHaveValue('10');
		expect(within(limitSelect()).getByRole('option', { selected: true })).toHaveTextContent(
			'10 A · 6,9 kW'
		);
	});

	it('ruft onNowLimit mit der Wahl und mit null für „Volle Leistung“', async () => {
		const props = show({ mode: 'now', nowLimit: SETUP });
		await fireEvent.change(limitSelect(), { target: { value: '10' } });
		expect(props.onNowLimit).toHaveBeenLastCalledWith(10);
		await fireEvent.change(limitSelect(), { target: { value: 'full' } });
		expect(props.onNowLimit).toHaveBeenLastCalledWith(null);
	});

	it('sperrt die Auswahl, solange die Karte beschäftigt ist', () => {
		show({ mode: 'now', nowLimit: SETUP, busy: true });
		expect(limitSelect()).toBeDisabled();
	});

	it('nennt das Limit in einem Satz, auch Lesenden', () => {
		show({ mode: 'now', nowLimit: { ...SETUP, max_a: 10 }, operate: false });
		expect(screen.getByText('Sofort mit höchstens 10 A (6,9 kW)')).toBeVisible();
	});

	it('nennt ohne Limit oder außerhalb von „Sofort“ keinen Satz', () => {
		show({ mode: 'now', nowLimit: SETUP });
		expect(screen.queryByText(/Sofort mit höchstens/)).toBeNull();
		cleanup();
		show({ mode: 'pv', nowLimit: { ...SETUP, max_a: 10 } });
		expect(screen.queryByText(/Sofort mit höchstens/)).toBeNull();
	});

	it('nennt das Limit Lesenden nicht, solange Boost gilt', () => {
		// Boost lädt mit voller Leistung, das Limit ruht (Entscheidung 3)
		show({
			mode: 'now',
			nowLimit: { ...SETUP, max_a: 10 },
			operate: false,
			boost: { active: true, until: '2026-10-08T12:00:00Z', pending: false }
		});
		expect(screen.queryByText(/Sofort mit höchstens/)).toBeNull();
	});

	it('zeigt einen Fehler der Karte neben dem Limit', () => {
		show({
			mode: 'now',
			nowLimit: SETUP,
			notice: { level: 'error', text: 'Sofort-Höchststrom „3“ liegt außerhalb von 5–16 A.' }
		});
		expect(screen.getByText('Sofort-Höchststrom „3“ liegt außerhalb von 5–16 A.')).toBeVisible();
	});

	it('zeigt die Meldung gleich unter „Laden einstellen“, vor Vorschlag und Details', () => {
		show({
			mode: 'planned',
			planned: PLANNED,
			driving: drivingVehicle('tesla'),
			notice: { level: 'error', text: 'Ziel abgelehnt.' }
		});
		const follows = (a: Node, b: Node) =>
			Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
		const notice = screen.getByText('Ziel abgelehnt.');
		expect(follows(screen.getByRole('button', { name: 'Ziel speichern' }), notice)).toBe(true);
		expect(
			follows(notice, screen.getByRole('heading', { name: 'Vorschlag aus dem Fahrverhalten' }))
		).toBe(true);
		expect(follows(notice, screen.getByText('Details'))).toBe(true);
	});
});

describe('Aufbau der Karte', () => {
	const NB = ' ';
	const ASSUMED_PLANNED = {
		text: `Fertig um 16:30 · Ziel 60${NB}% bis 17:30`,
		notes: [`Ladestand unbekannt, gerechnet ab 0${NB}%.`]
	};
	const STALE = { id: 'tesla', label: 'Tesla Model 3', pct: 70, quality: 'stale' as const };

	function vehicleWith(changes: Partial<Props['vehicle']>): Props['vehicle'] {
		return { ...system().core.health!.vehicles.find((item) => item.id === 'tesla')!, ...changes };
	}

	it('hat genau die beiden eindeutigen Knöpfe und keinen „Übernehmen“', () => {
		show({ mode: 'planned', planned: PLANNED, driving: drivingVehicle('tesla') });
		expect(screen.getByRole('button', { name: 'Ziel speichern' })).toBeEnabled();
		expect(screen.getByRole('button', { name: 'Vorschlag übernehmen' })).toBeEnabled();
		expect(screen.queryByRole('button', { name: 'Übernehmen' })).toBeNull();
	});

	it('nennt Wallbox, Zuordnung und Modus unter dem Namen', () => {
		show({ vehicle: vehicleWith({ mode: 'planned', assignment: 'detected' }) });
		expect(screen.getByText('Garage (erkannt) · Geplant')).toBeVisible();
	});

	it('zeigt den Stand des Fahrzeugs als Etikett', () => {
		show({ vehicle: vehicleWith({ plugged: true, charging: true }) });
		expect(screen.getByText('lädt')).toBeVisible();
		cleanup();
		show({ vehicle: vehicleWith({ plugged: false, charging: false }) });
		expect(screen.getByText('nicht angesteckt')).toBeVisible();
		cleanup();
		show({ vehicle: vehicleWith({ plugged: null, charging: null }) });
		expect(screen.getByText('Stand unbekannt')).toBeVisible();
	});

	it('hält Angesteckt, Lädt, Grenze, Grund und Befehle in einem geschlossenen „Details“', () => {
		show({
			vehicle: vehicleWith({ plugged: true, charging: false, limit_a: 10, commands_hour: 3 })
		});
		const details = screen.getByText('Grenze').closest('details');
		expect(details).not.toBeNull();
		expect(details).not.toHaveAttribute('open');
		expect(within(details!).getByText('Details')).toBeInTheDocument();
		for (const label of [
			'Angesteckt',
			'Lädt',
			'Grenze',
			'Letzter Grund',
			'Befehle letzte Stunde'
		]) {
			expect(within(details!).getByText(label)).toBeInTheDocument();
		}
	});

	it('zeigt den Ladestand groß mit Balken und Ziel in „Geplant“', () => {
		show({
			mode: 'planned',
			planned: PLANNED,
			soc: { id: 'tesla', label: 'Tesla Model 3', pct: 45, quality: 'ok' }
		});
		expect(screen.getByText('45 %')).toBeVisible();
		expect(screen.getByText('Ziel 60 %')).toBeVisible();
		expect(
			screen.getByRole('img', { name: `Ladestand 45${NB}%, Ziel 60${NB}%` })
		).toBeInTheDocument();
	});

	it('zeigt außerhalb von „Geplant“ kein Ziel im Balken', () => {
		show({ mode: 'pv', soc: { id: 'tesla', label: 'Tesla Model 3', pct: 45, quality: 'ok' } });
		expect(screen.queryByText(/^Ziel /)).toBeNull();
		expect(screen.getByRole('img', { name: `Ladestand 45${NB}%` })).toBeInTheDocument();
	});

	it('erklärt einen veralteten Ladestand statt „Ladestand unbekannt …“', () => {
		show({ mode: 'planned', planned: ASSUMED_PLANNED, soc: STALE });
		expect(screen.getByText('veraltet')).toBeVisible();
		expect(
			screen.getByText(
				'Der letzte Ladestand (70 %) ist zu alt. Der Plan rechnet deshalb vorsichtig ab 0 %.'
			)
		).toBeVisible();
		expect(screen.queryByText(/Ladestand unbekannt/)).toBeNull();
	});

	it('behält ohne Ladestand den Satz „Ladestand unbekannt, gerechnet ab 0 %.“', () => {
		show({ mode: 'planned', planned: ASSUMED_PLANNED, soc: undefined });
		expect(screen.getByText('Ladestand unbekannt, gerechnet ab 0 %.')).toBeVisible();
		expect(screen.queryByText(/ist zu alt/)).toBeNull();
	});
});
