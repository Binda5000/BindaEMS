import { render, screen } from '@testing-library/svelte';
import { expect, it } from 'vitest';
import SignalTable from './SignalTable.svelte';

it('markiert veraltete und ungültige Werte', () => {
	render(SignalTable, {
		rows: [
			{ name: 'vebus.mode', value: 'ON', quality: 'stale', ageS: 40 },
			{ name: 'battery.soc_pct', value: '–', quality: 'invalid', ageS: null }
		]
	});
	expect(screen.getByText('veraltet')).toBeInTheDocument();
	expect(screen.getByText('ungültig')).toBeInTheDocument();
	expect(screen.getByText('vor 40 s')).toBeInTheDocument();
});
