import { render, screen } from '@testing-library/svelte';
import { expect, it } from 'vitest';
import SplitBar from './SplitBar.svelte';

it('beschreibt die Anteile in ganzen Prozent und setzt die Breiten', () => {
	const { container } = render(SplitBar, { pv: 8.1, battery: 2, grid: 2.5 });
	expect(screen.getByRole('img', { name: 'PV 64 %, Akku 16 %, Netz 20 %' })).toBeInTheDocument();
	const widths = [...container.querySelectorAll<HTMLElement>('[data-part]')].map((part) => [
		part.dataset.part,
		part.style.width
	]);
	expect(widths).toEqual([
		['pv', '64.29%'],
		['battery', '15.87%'],
		['grid', '19.84%']
	]);
});

it('zeigt ohne Energie einen leeren Balken', () => {
	const { container } = render(SplitBar, { pv: 0, battery: 0, grid: 0 });
	expect(screen.getByRole('img', { name: 'keine Energie' })).toBeInTheDocument();
	expect(container.querySelectorAll('[data-part]')).toHaveLength(0);
});
