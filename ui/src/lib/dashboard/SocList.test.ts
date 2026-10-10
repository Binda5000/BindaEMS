import { render, screen } from '@testing-library/svelte';
import { expect, it } from 'vitest';
import { ApiError } from '$lib/api/errors';
import SocList from './SocList.svelte';

it('zeigt, warum die Fahrzeuge fehlen', () => {
	const entries = [{ id: 'battery', label: 'Akku', pct: 55, quality: 'ok' as const }];
	render(SocList, { entries, error: new ApiError(502, 'Server nicht erreichbar (HTTP 502)') });
	expect(screen.getByText('Akku')).toBeInTheDocument();
	expect(screen.getByText(/Server nicht erreichbar/)).toBeInTheDocument();
});
