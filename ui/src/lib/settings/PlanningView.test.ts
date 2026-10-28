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
