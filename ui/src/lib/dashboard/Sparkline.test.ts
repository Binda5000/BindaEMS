import { render } from '@testing-library/svelte';
import { expect, it } from 'vitest';
import Sparkline from './Sparkline.svelte';

it('zeichnet die Polylinie auf 100 × 24 und versteckt sie vor Screenreadern', () => {
	const { container } = render(Sparkline, {
		points: [
			[0, 0],
			[10, 10],
			[20, 5]
		],
		color: 'var(--pv)'
	});
	const svg = container.querySelector('svg');
	expect(svg).toHaveAttribute('aria-hidden', 'true');
	expect(svg).toHaveAttribute('viewBox', '0 0 100 24');
	expect(container.querySelector('polyline')).toHaveAttribute('points', '0,22 50,2 100,12');
});

it('zeichnet bei weniger als zwei Punkten nichts', () => {
	const { container } = render(Sparkline, { points: [[0, 1]], color: 'var(--pv)' });
	expect(container.querySelector('svg')).toBeNull();
});
