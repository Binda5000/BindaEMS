import { render, screen } from '@testing-library/svelte';
import { expect, it } from 'vitest';
import { stats } from '$lib/testing/fixtures';
import StatTiles from './StatTiles.svelte';
import { tiles } from './view';

it('zeigt fünf Kacheln mit Wert, Ersparnis mit Untertitel', () => {
	render(StatTiles, { tiles: tiles(stats()) });
	const items = screen.getAllByTestId('tile');
	expect(items).toHaveLength(5);
	expect(items[0]).toHaveTextContent('Autarkie');
	expect(items[0]).toHaveTextContent('66 %');
	expect(items[3]).toHaveTextContent('Ersparnis');
	expect(items[3]).toHaveTextContent('gegenüber Fixtarif ohne Anlage');
	expect(items[1]).not.toHaveTextContent('gegenüber');
});

it('zeigt unbekannte Werte als Strich', () => {
	const base = stats();
	render(StatTiles, {
		tiles: tiles({ ...base, ratios: { autarky: null, self_consumption: null } })
	});
	expect(screen.getAllByTestId('tile')[0]).toHaveTextContent('–');
	expect(screen.getAllByTestId('tile')[1]).toHaveTextContent('–');
});
