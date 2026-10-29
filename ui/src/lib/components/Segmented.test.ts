import { fireEvent, render, screen } from '@testing-library/svelte';
import { expect, it, vi } from 'vitest';
import Segmented from './Segmented.svelte';

const options = [
	{ id: 7, label: '7 Tage' },
	{ id: 14, label: '14 Tage' },
	{ id: 28, label: '28 Tage' },
	{ id: 60, label: '60 Tage' }
];

it('zeigt alle Optionen als Knöpfe einer benannten Gruppe', () => {
	render(Segmented, { options, value: 14, label: 'Zeitraum wählen', onSelect: vi.fn() });
	const group = screen.getByRole('group', { name: 'Zeitraum wählen' });
	expect(group).toBeInTheDocument();
	expect(screen.getAllByRole('button').map((b) => b.textContent?.trim())).toEqual([
		'7 Tage',
		'14 Tage',
		'28 Tage',
		'60 Tage'
	]);
});

it('markiert nur den gewählten Knopf', () => {
	render(Segmented, { options, value: 14, label: 'Zeitraum', onSelect: vi.fn() });
	const pressed = screen
		.getAllByRole('button')
		.map((b) => [b.textContent?.trim(), b.getAttribute('aria-pressed')]);
	expect(pressed).toEqual([
		['7 Tage', 'false'],
		['14 Tage', 'true'],
		['28 Tage', 'false'],
		['60 Tage', 'false']
	]);
});

it('meldet beim Klick die Kennung der Option', async () => {
	const onSelect = vi.fn();
	render(Segmented, { options, value: 14, label: 'Zeitraum', onSelect });
	await fireEvent.click(screen.getByRole('button', { name: '28 Tage' }));
	expect(onSelect).toHaveBeenCalledExactlyOnceWith(28);
});
