import { render, screen } from '@testing-library/svelte';
import { expect, it } from 'vitest';
import { settingsCurrent } from '$lib/testing/fixtures';
import PlanningView from './PlanningView.svelte';

it('zeigt die Planung mit Einheiten und Dezimalkomma', () => {
	const planning = { ...settingsCurrent().settings.planning, grid_charge_allowed: true };
	render(PlanningView, { planning });
	expect(screen.getByText('Netzladen erlaubt').nextElementSibling).toHaveTextContent('ja');
	expect(screen.getByText('Marge').nextElementSibling).toHaveTextContent('2,0 ct/kWh');
	expect(screen.getByText('Roundtrip-Wirkungsgrad').nextElementSibling).toHaveTextContent('88 %');
	expect(screen.getByText('Verschleiß').nextElementSibling).toHaveTextContent('2,0 ct/kWh');
	expect(screen.getByText('Vorsichtsfaktor Restwert').nextElementSibling).toHaveTextContent('0,90');
	expect(screen.getByText('Planträgheit').nextElementSibling).toHaveTextContent('0,5 ct/kWh');
});

it('zeigt „nein“ bei ausgeschaltetem Netzladen', () => {
	render(PlanningView, { planning: settingsCurrent().settings.planning });
	expect(screen.getByText('Netzladen erlaubt').nextElementSibling).toHaveTextContent('nein');
});

it('zeigt Akku ins Auto, wenn übrig, als ja oder nein', () => {
	const planning = settingsCurrent().settings.planning;
	const { unmount } = render(PlanningView, { planning });
	expect(screen.getByText('Akku ins Auto, wenn übrig').nextElementSibling).toHaveTextContent(
		'nein'
	);
	unmount();
	render(PlanningView, { planning: { ...planning, car_battery_allowed: true } });
	expect(screen.getByText('Akku ins Auto, wenn übrig').nextElementSibling).toHaveTextContent('ja');
});

it('zeigt den gedachten Satz für die Monatsspitze, bei 0 „aus“', () => {
	const planning = settingsCurrent().settings.planning;
	const label = 'Spitze vermeiden ohne Leistungspreis';
	const { unmount } = render(PlanningView, { planning });
	expect(screen.getByText(label).nextElementSibling).toHaveTextContent('100 ct je kW und Monat');
	unmount();
	render(PlanningView, { planning: { ...planning, peak_soft_ct_per_kw: 0 } });
	expect(screen.getByText(label).nextElementSibling).toHaveTextContent('aus');
});

it('zeigt die Unwetter-Reserve: aus, Ziel, Stufe und Warnarten', () => {
	const planning = settingsCurrent().settings.planning;
	const { unmount } = render(PlanningView, { planning });
	expect(screen.getByRole('heading', { name: 'Unwetter-Reserve' })).toBeInTheDocument();
	const value = (label: string) => screen.getByText(label).nextElementSibling;
	expect(value('Eingeschaltet')).toHaveTextContent('nein');
	expect(value('Ziel-SOC')).toHaveTextContent('40 %');
	expect(value('Warnstufe')).toHaveTextContent('ab orange');
	expect(value('Warnarten')).toHaveTextContent('Sturm, Gewitter');
	unmount();
	const storm = {
		enabled: true,
		target_soc_pct: 55,
		min_level: 3 as const,
		kinds: ['kaelte' as const]
	};
	render(PlanningView, { planning: { ...planning, storm } });
	expect(value('Eingeschaltet')).toHaveTextContent('ja');
	expect(value('Ziel-SOC')).toHaveTextContent('55 %');
	expect(value('Warnstufe')).toHaveTextContent('nur rot');
	expect(value('Warnarten')).toHaveTextContent('Kälte');
});
