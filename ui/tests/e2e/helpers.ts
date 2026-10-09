// Gemeinsames für die End-to-End-Abläufe: Demo-Zugänge, Anmeldung, TOTP, Beobachtung der Seite
import { createHmac } from 'node:crypto';
import type { Page } from '@playwright/test';

/** Uhr des Demo-Backends (09.10.2026, 10:00 in Wien) */
export const T_DEMO = new Date('2026-10-09T08:00:00Z');
export const DEMO_PASSWORD = 'demo-passwort-1';
export const DEMO_TOTP_SECRET = 'JBSWY3DPEHPK3PXP';

/** Anmelden ohne TOTP; wartet, bis die Anmeldeseite verlassen ist. */
export async function login(page: Page, username: string): Promise<void> {
	await page.goto('/login');
	await page.getByLabel('Benutzername').fill(username);
	await page.getByLabel('Passwort').fill(DEMO_PASSWORD);
	await page.getByRole('button', { name: 'Anmelden' }).click();
	await page.waitForURL((url) => !url.pathname.startsWith('/login'));
}

function base32(text: string): Buffer {
	const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';
	let bits = '';
	for (const char of text.replace(/=+$/, '').toUpperCase()) {
		const value = alphabet.indexOf(char);
		if (value < 0) throw new Error(`kein Base32-Zeichen: ${char}`);
		bits += value.toString(2).padStart(5, '0');
	}
	const bytes: number[] = [];
	for (let i = 0; i + 8 <= bits.length; i += 8) bytes.push(parseInt(bits.slice(i, i + 8), 2));
	return Buffer.from(bytes);
}

/** TOTP nach RFC 6238: HMAC-SHA-1, 30 s, 6 Stellen */
export function totp(secret: string, at: Date): string {
	const counter = Buffer.alloc(8);
	counter.writeBigUInt64BE(BigInt(Math.floor(at.getTime() / 30_000)));
	const hmac = createHmac('sha1', base32(secret)).update(counter).digest();
	const offset = hmac[hmac.length - 1] & 0x0f;
	const code = (hmac.readUInt32BE(offset) & 0x7fffffff) % 1_000_000;
	return String(code).padStart(6, '0');
}

/** Sammelt CSP-Verstöße und andere Fehler der Seite. */
export function watchPage(page: Page): { cspViolations: string[]; errors: string[] } {
	const watch = { cspViolations: [] as string[], errors: [] as string[] };
	page.on('console', (message) => {
		if (message.type() !== 'error') return;
		const text = message.text();
		if (text.includes('Content Security Policy')) watch.cspViolations.push(text);
		else watch.errors.push(text);
	});
	page.on('pageerror', (error) => watch.errors.push(error.message));
	return watch;
}
