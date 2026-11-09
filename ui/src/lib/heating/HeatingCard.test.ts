import { render, screen, within } from '@testing-library/svelte';
import { expect, it } from 'vitest';
import { heatingStats } from '$lib/testing/heating';
import HeatingCard from './HeatingCard.svelte';

const rowOf = (label: string) => {
	const row = screen.getByText(label, { selector: 'th' }).closest('tr');
	if (!row) throw new Error(label);
	return row;
};

it('zeigt die Zeilen mit Komma und Einheit', () => {
	render(HeatingCard, { heating: heatingStats(), first: '2026-10-01' });
	expect(screen.getByRole('heading', { name: 'Heizung', level: 2 })).toBeInTheDocument();
	expect(rowOf('Brenner lief')).toHaveTextContent('120,5 h');
	expect(rowOf('Wärme aus Öl')).toHaveTextContent('2 530,5 kWh');
	expect(rowOf('Öl')).toHaveTextContent('722,5 l · 794,75 €');
	expect(rowOf('Klimaanlage heizt')).toHaveTextContent('80,3 kWh Strom');
	expect(rowOf('Klimaanlage heizt')).toHaveTextContent('≈ 240,8 kWh Wärme');
	expect(rowOf('Wärmebedarf')).toHaveTextContent('2 771,3 kWh');
	expect(rowOf('Heizgradtage')).toHaveTextContent('310,4 K·d');
	expect(rowOf('Heizgradtage')).toHaveTextContent('Heizgrenze 15 °C');
	expect(rowOf('Vollständige Tage')).toHaveTextContent('30 von 31');
	expect(screen.queryByText(/Nennleistung fehlt/)).not.toBeInTheDocument();
	expect(screen.queryByText(/Brennerstatus ab/)).not.toBeInTheDocument();
});

it('nennt bei fehlender Nennleistung die Einstellung und zeigt Striche statt 0', () => {
	render(HeatingCard, {
		heating: heatingStats({
			settings_missing: true,
			oil_heat_kwh: null,
			oil_l: null,
			oil_eur: null,
			ac_heat_el_kwh: null,
			ac_heat_kwh: null,
			demand_kwh: null
		}),
		first: '2026-10-01'
	});
	expect(
		screen.getByText('Nennleistung fehlt: unter Einstellungen → Ölheizung eintragen.')
	).toBeInTheDocument();
	expect(within(rowOf('Wärme aus Öl')).getByText('–')).toBeInTheDocument();
	expect(rowOf('Öl')).toHaveTextContent(/^Öl–$/);
	expect(rowOf('Klimaanlage heizt')).toHaveTextContent(/^Klimaanlage heizt–$/);
	expect(rowOf('Wärmebedarf')).toHaveTextContent(/^Wärmebedarf–$/);
	expect(rowOf('Brenner lief')).toHaveTextContent('120,5 h');
});

it('lässt den Preis weg, wenn er fehlt, und nennt den Beginn des Brennerstatus', () => {
	render(HeatingCard, {
		heating: heatingStats({ oil_eur: null, first_day: '2026-10-05' }),
		first: '2026-10-01'
	});
	expect(rowOf('Öl')).toHaveTextContent(/^Öl722,5 l$/);
	expect(screen.getByText('Brennerstatus ab 05.10.2026.')).toBeInTheDocument();
});
