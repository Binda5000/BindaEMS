import { render, screen, within } from '@testing-library/svelte';
import { expect, it } from 'vitest';
import { settingsCurrent } from '$lib/testing/fixtures';
import LearningView from './LearningView.svelte';

it('zeigt Schalter und gespeicherte Sensoren nur lesbar', () => {
	render(LearningView, { learning: settingsCurrent().settings.learning });
	expect(screen.getByText('Lastmodell verwenden').nextElementSibling).toHaveTextContent('ja');
	const list = screen.getByRole('list', { name: 'Gewählte Sensoren' });
	expect(
		within(list)
			.getAllByRole('listitem')
			.map((item) => item.textContent)
	).toEqual(['sensor.aussentemperatur', 'sensor.wohnzimmer_temperatur']);
	expect(screen.queryByRole('checkbox')).toBeNull();
	expect(screen.queryByRole('button')).toBeNull();
});

it('zeigt „nein“ und „keine“ ohne Modell und Sensoren', () => {
	render(LearningView, { learning: { load_model_enabled: false, ha_sensors: [] } });
	expect(screen.getByText('Lastmodell verwenden').nextElementSibling).toHaveTextContent('nein');
	expect(
		screen.getByText('Zusätzliche Sensoren aus Home Assistant').nextElementSibling
	).toHaveTextContent('keine');
});

it('zeigt das Fahrverhalten nur lesbar, ohne die Schalter der Automatik', () => {
	const learning = settingsCurrent().settings.learning;
	render(LearningView, {
		learning: {
			...learning,
			driving: {
				...learning.driving!,
				presence_entities: { tesla: 'device_tracker.tesla' }
			}
		},
		vehicles: { tesla: { name: 'Tesla Model 3' }, egolf: { name: 'e-Golf' } }
	});
	expect(screen.getByRole('heading', { name: 'Fahrverhalten' })).toBeVisible();
	expect(screen.getByText('Puffer bei der Rückkehr (%)').nextElementSibling).toHaveTextContent(
		'20'
	);
	expect(screen.getByText('Ziel-SOC mindestens (%)').nextElementSibling).toHaveTextContent('50');
	expect(screen.getByText('Ziel-SOC höchstens (%)').nextElementSibling).toHaveTextContent('90');
	expect(
		screen.getByText('Anwesenheit aus Home Assistant (Tesla Model 3)').nextElementSibling
	).toHaveTextContent('device_tracker.tesla');
	expect(
		screen.getByText('Anwesenheit aus Home Assistant (e-Golf)').nextElementSibling
	).toHaveTextContent('–');
	expect(screen.queryByRole('checkbox')).toBeNull();
});

it('zeigt ohne den Abschnitt die Standardwerte', () => {
	render(LearningView, { learning: { load_model_enabled: false, ha_sensors: [] } });
	expect(screen.getByText('Puffer bei der Rückkehr (%)').nextElementSibling).toHaveTextContent(
		'20'
	);
});
