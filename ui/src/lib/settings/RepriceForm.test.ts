import { fireEvent, render, screen } from '@testing-library/svelte';
import { expect, it, vi } from 'vitest';
import RepriceForm from './RepriceForm.svelte';

it('bewertet den gewählten Zeitraum neu', async () => {
	const reprice = vi.fn().mockResolvedValueOnce({ repriced: 2976 });
	render(RepriceForm, { initial: { first: '2026-10-01', last: '2026-10-09' }, reprice });
	await fireEvent.click(screen.getByRole('button', { name: 'Neu bewerten' }));
	expect(reprice).toHaveBeenCalledWith('2026-10-01', '2026-10-09');
	expect(await screen.findByText('2976 Viertelstunden neu bewertet.')).toBeInTheDocument();
});
