import { render, screen } from '@testing-library/svelte';
import { expect, it } from 'vitest';
import { settingsCurrent } from '$lib/testing/fixtures';
import TariffView from './TariffView.svelte';

it('zeigt Leistungspreis und Gebühr ohne Wert als fehlend', () => {
	render(TariffView, { settings: settingsCurrent().settings });
	const power = screen.getByText('Leistungspreis (ElWG)').closest('tr');
	const fee = screen.getByText('Grundgebühr Netz').closest('tr');
	expect(power).toHaveTextContent('Leistungspreis');
	expect(power).toHaveTextContent('fehlt');
	expect(power).toHaveTextContent('ab 01.01.2027');
	expect(fee).toHaveTextContent('Gebühr');
	expect(fee).toHaveTextContent('fehlt');
});

it('zeigt Werte mit Einheit', () => {
	const settings = settingsCurrent().settings;
	settings.tariff.components = settings.tariff.components.map((c) =>
		c.kind === 'power_per_kw_year'
			? { ...c, value_eur: 19 }
			: c.kind === 'fixed_per_month'
				? { ...c, value_eur: 4.5 }
				: c
	);
	render(TariffView, { settings });
	expect(screen.getByText('Leistungspreis (ElWG)').closest('tr')).toHaveTextContent('€/kW/Jahr');
	expect(screen.getByText('Grundgebühr Netz').closest('tr')).toHaveTextContent('€/Monat');
});
