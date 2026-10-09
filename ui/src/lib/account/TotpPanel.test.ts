import { fireEvent, render, screen } from '@testing-library/svelte';
import { expect, it, vi } from 'vitest';
import { ApiError } from '$lib/api/errors';
import TotpPanel from './TotpPanel.svelte';

it('richtet TOTP mit Passwort, QR-Code und Code ein', async () => {
	const setup = vi.fn().mockResolvedValueOnce({
		secret: 'JBSWY3DPEHPK3PXP',
		uri: 'otpauth://totp/BindaEMS:gast?secret=JBSWY3DPEHPK3PXP&issuer=BindaEMS'
	});
	const enable = vi.fn().mockResolvedValueOnce(undefined);
	const onChanged = vi.fn();
	render(TotpPanel, { enabled: false, setup, enable, disable: vi.fn(), onChanged });
	await fireEvent.click(screen.getByRole('button', { name: 'Einrichten' }));
	await fireEvent.input(screen.getByLabelText('Passwort'), {
		target: { value: 'demo-passwort-1' }
	});
	await fireEvent.click(screen.getByRole('button', { name: 'Weiter' }));
	expect(await screen.findByAltText('QR-Code für die Authenticator-App')).toBeInTheDocument();
	expect(screen.getByText('JBSW Y3DP EHPK 3PXP')).toBeInTheDocument();
	await fireEvent.input(screen.getByLabelText('Bestätigungscode'), { target: { value: '123456' } });
	await fireEvent.click(screen.getByRole('button', { name: 'Aktivieren' }));
	expect(setup).toHaveBeenCalledWith('demo-passwort-1');
	expect(enable).toHaveBeenCalledWith('123456');
	expect(onChanged).toHaveBeenCalled();
});

it('verlangt zum Abschalten das Passwort', async () => {
	const disable = vi.fn().mockRejectedValueOnce(new ApiError(400, 'Passwort falsch'));
	render(TotpPanel, {
		enabled: true,
		setup: vi.fn(),
		enable: vi.fn(),
		disable,
		onChanged: vi.fn()
	});
	await fireEvent.click(screen.getByRole('button', { name: 'Abschalten' }));
	await fireEvent.input(screen.getByLabelText('Passwort'), { target: { value: 'falsch-falsch' } });
	await fireEvent.click(screen.getByRole('button', { name: 'Zwei-Faktor-Anmeldung abschalten' }));
	expect(await screen.findByText('Passwort falsch')).toBeInTheDocument();
});
