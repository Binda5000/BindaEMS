import { render, screen } from '@testing-library/svelte';
import { expect, it } from 'vitest';
import { settingsCurrent } from '$lib/testing/fixtures';
import TariffView from './TariffView.svelte';

it('zeigt Leistungspreis und Gebühr ohne Wert als fehlend', () => {
	const settings = settingsCurrent().settings;
	// die Demo hat einen Leistungspreis und eine Grundgebühr mit Satz; hier fehlt der Satz: beim
	// Leistungspreis gilt er erst ab 2027
	settings.tariff.components = settings.tariff.components.map((c) =>
		c.kind === 'power_per_kw_year'
			? { ...c, value_eur: null, valid_from: '2027-01-01' }
			: c.kind === 'fixed_per_month'
				? { ...c, value_eur: null }
				: c
	);
	render(TariffView, { settings });
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

it('zeigt Stufe, Satz über der Stufe und Mindestverrechnung des Leistungspreises', () => {
	const settings = settingsCurrent().settings;
	settings.tariff.components = settings.tariff.components.map((c) =>
		c.kind === 'power_per_kw_year'
			? { ...c, value_eur: 19, value_above_eur: 34.5, tier_kw: 10, minimum_kw: 2 }
			: c
	);
	render(TariffView, { settings });
	const value = screen.getByText('Leistungspreis (ElWG)').closest('tr')?.querySelector('td.num');
	expect(value?.textContent?.replace(/\s/g, ' ')).toBe(
		'19,00 €/kW/Jahr, über 10 kW 34,50 €/kW/Jahr, mind. 2 kW'
	);
});

it('zeigt ohne eigenen Satz über der Stufe nur Satz und Mindestverrechnung', () => {
	const settings = settingsCurrent().settings;
	settings.tariff.components = settings.tariff.components.map((c) =>
		c.kind === 'power_per_kw_year'
			? { ...c, value_eur: 19, value_above_eur: null, tier_kw: 10, minimum_kw: 2 }
			: c
	);
	render(TariffView, { settings });
	const value = screen.getByText('Leistungspreis (ElWG)').closest('tr')?.querySelector('td.num');
	expect(value?.textContent?.replace(/\s/g, ' ')).toBe('19,00 €/kW/Jahr, mind. 2 kW');
});

it('nennt die Art mit denselben Bezeichnungen wie der Editor', () => {
	render(TariffView, { settings: settingsCurrent().settings });
	expect(screen.getByText('Leistungspreis (ElWG)').closest('tr')).toHaveTextContent(
		'Leistungspreis (€/kW/Jahr)'
	);
	expect(screen.getByText('Grundgebühr Netz').closest('tr')).toHaveTextContent(
		'Monatliche Gebühr (€/Monat)'
	);
});
