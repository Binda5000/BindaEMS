import { render, screen } from '@testing-library/svelte';
import { expect, it } from 'vitest';
import { stats } from '$lib/testing/fixtures';
import StatTiles from './StatTiles.svelte';
import { tiles } from './view';

it('zeigt fünf Kacheln mit Wert, Ersparnis mit Stern und ohne Untertitel', () => {
	render(StatTiles, { tiles: tiles(stats()) });
	const items = screen.getAllByTestId('tile');
	expect(items).toHaveLength(5);
	expect(items[0]).toHaveTextContent('Autarkie');
	expect(items[0]).toHaveTextContent('66 %');
	expect(items[3]).toHaveTextContent('Ersparnis*');
	expect(items[4]).toHaveTextContent('Erlös');
	for (const item of items) expect(item).not.toHaveTextContent('gegenüber');
});

it('gibt Autarkie und Eigenverbrauch große, die übrigen kleine Kacheln', () => {
	render(StatTiles, { tiles: tiles(stats()) });
	const sizes = screen.getAllByTestId('tile').map((item) => item.dataset.size);
	expect(sizes).toEqual(['large', 'large', 'small', 'small', 'small']);
});

it('erklärt Stern und Erlös in einer Fußnote', () => {
	render(StatTiles, { tiles: tiles(stats()) });
	expect(
		screen.getByText('* gegenüber Fixtarif ohne Anlage. „Erlös“ ist der Einspeiseerlös.')
	).toBeVisible();
});

it('zeigt unbekannte Werte als Strich', () => {
	const base = stats();
	render(StatTiles, {
		tiles: tiles({ ...base, ratios: { autarky: null, self_consumption: null } })
	});
	expect(screen.getAllByTestId('tile')[0]).toHaveTextContent('–');
	expect(screen.getAllByTestId('tile')[1]).toHaveTextContent('–');
});
