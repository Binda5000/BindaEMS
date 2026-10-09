import { expect, it } from 'vitest';
import { loginHref, safeNext } from './navigation';

it('leitet nur auf Pfade dieser App weiter', () => {
	expect(safeNext('/verlauf?tag=2026-10-09')).toBe('/verlauf?tag=2026-10-09');
	for (const bad of ['//boese.example', 'https://boese.example', '/\\boese.example', '', null]) {
		expect(safeNext(bad)).toBe('/');
	}
});

it('401 führt zur Anmeldung mit abgelaufen=1', () => {
	const url = new URL('https://ems.lan/system?ansicht=signale');
	expect(loginHref(url)).toBe('/login?next=%2Fsystem%3Fansicht%3Dsignale');
	expect(loginHref(url, true)).toBe('/login?next=%2Fsystem%3Fansicht%3Dsignale&abgelaufen=1');
});
