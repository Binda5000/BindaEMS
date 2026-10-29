import { fireEvent, render, screen } from '@testing-library/svelte';
import { expect, it } from 'vitest';
import { learningStatus } from '$lib/testing/fixtures';
import FeatureTable from './FeatureTable.svelte';

it('zeigt die ersten fünf Merkmale mit Anteil und einen Knopf für den Rest', async () => {
	render(FeatureTable, { status: learningStatus() });
	expect(screen.getByRole('heading', { name: 'Was das Modell nutzt' })).toBeInTheDocument();
	expect(screen.getAllByRole('listitem').filter((li) => li.querySelector('.share'))).toHaveLength(
		5
	);
	expect(screen.getByText('Temperatur')).toBeInTheDocument();
	expect(screen.getByText('28,0 %')).toBeInTheDocument();
	expect(screen.queryByText('Tagesmittel der Temperatur')).toBeNull();

	await fireEvent.click(screen.getByRole('button', { name: 'alle 14 Merkmale zeigen' }));
	expect(screen.getByText('Tagesmittel der Temperatur')).toBeInTheDocument();
	expect(screen.getByText('Aussentemperatur (Sensor)')).toBeInTheDocument();
});

it('nennt ungenutzte Sensoren lesbar', () => {
	render(FeatureTable, { status: learningStatus() });
	expect(screen.getByText('Wohnzimmer temperatur (Sensor): nicht im Modell')).toBeInTheDocument();
});
