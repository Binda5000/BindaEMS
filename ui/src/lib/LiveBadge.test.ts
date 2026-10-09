import { render, screen } from '@testing-library/svelte';
import { expect, it } from 'vitest';
import LiveBadge from './LiveBadge.svelte';

it.each([
	[{ status: 'open', stale: false, coreConnected: true }, 'live'],
	[{ status: 'open', stale: true, coreConnected: false }, 'core getrennt'],
	[{ status: 'open', stale: true, coreConnected: true }, 'veraltet'],
	[{ status: 'reconnecting', stale: true, coreConnected: true }, 'verbinde …'],
	[{ status: 'unauthorized', stale: true, coreConnected: false }, 'abgemeldet']
] as const)('zeigt %o als „%s“', (props, text) => {
	render(LiveBadge, props);
	expect(screen.getByText(text)).toBeInTheDocument();
});
