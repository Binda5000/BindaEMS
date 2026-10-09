import { expect, it } from 'vitest';
import { validateNewUser } from './form';

it('prüft Benutzername und Passwort wie der Server', () => {
	expect(validateNewUser({ username: ' Max ', password: 'kurz', role: 'viewer' })).toEqual({
		errors: { password: 'Mindestens 10 Zeichen' }
	});
	expect(validateNewUser({ username: 'm', password: 'lang-genug-1', role: 'viewer' })).toEqual({
		errors: { username: '3–32 Zeichen aus a–z, 0–9, Punkt, Bindestrich, Unterstrich' }
	});
	expect(
		validateNewUser({ username: ' Max ', password: 'lang-genug-1', role: 'operator' })
	).toEqual({
		body: { username: 'max', password: 'lang-genug-1', role: 'operator' }
	});
});
