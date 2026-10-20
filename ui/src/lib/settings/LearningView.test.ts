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
