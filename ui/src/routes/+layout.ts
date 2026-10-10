// Reines SPA: nichts wird vorgerendert, alles läuft im Browser. Ohne Sitzung geht es zur Anmeldung.
import { redirect } from '@sveltejs/kit';
import { api } from '$lib/api/endpoints';
import { ApiError } from '$lib/api/errors';
import type { User } from '$lib/api/schemas';
import { loginHref } from '$lib/navigation';
import type { LayoutLoad } from './$types';

export const ssr = false;
export const prerender = false;

// zuletzt bestätigter Benutzer: ist der Server kurz weg (Neustart, Netz), bleibt die Seite stehen
// und zeigt ihre eigenen Fehler, statt auf eine Fehlerseite zu wechseln
let lastUser: User | null = null;

export const load: LayoutLoad = async ({ url }): Promise<{ user: User | null }> => {
	if (url.pathname === '/login') {
		lastUser = null;
		return { user: null };
	}
	try {
		const { user } = await api.me({ redirectOn401: false });
		lastUser = user;
		return { user };
	} catch (error) {
		if (error instanceof ApiError && error.status === 401) redirect(307, loginHref(url));
		if (lastUser !== null) return { user: lastUser };
		throw error;
	}
};
