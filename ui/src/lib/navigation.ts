// Weiterleitungen rund um die Anmeldung

function hasControlCharacter(text: string): boolean {
	return [...text].some((char) => char.charCodeAt(0) < 0x20 || char.charCodeAt(0) === 0x7f);
}

/** Ziel nach der Anmeldung: nur Pfade dieser App, nie eine fremde Seite. */
export function safeNext(next: string | null | undefined): string {
	if (!next || !next.startsWith('/') || next.startsWith('//') || next.startsWith('/\\')) return '/';
	return hasControlCharacter(next) ? '/' : next; // Browser entfernen Tab und Zeilenumbruch
}

/** Anmeldeseite, die danach zu `url` zurückführt. */
export function loginHref(url: URL, expired = false): string {
	const params = new URLSearchParams({ next: url.pathname + url.search });
	if (expired) params.set('abgelaufen', '1');
	return `/login?${params}`;
}
