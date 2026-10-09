// Darstellung hell, dunkel oder wie das System; die Wahl liegt im localStorage.
// app.html setzt `data-theme` schon vor dem ersten Zeichnen (kein Flackern).

export type ThemePref = 'system' | 'light' | 'dark';

export const THEME_KEY = 'bindaems.theme';
const ORDER: readonly ThemePref[] = ['system', 'light', 'dark'];

function storage(): Storage | null {
	try {
		return typeof localStorage === 'undefined' ? null : localStorage;
	} catch {
		return null; // Speicher gesperrt
	}
}

export function readThemePref(source: Pick<Storage, 'getItem'> | null = storage()): ThemePref {
	try {
		const value = source?.getItem(THEME_KEY);
		return value === 'light' || value === 'dark' ? value : 'system';
	} catch {
		return 'system';
	}
}

export function nextThemePref(pref: ThemePref): ThemePref {
	return ORDER[(ORDER.indexOf(pref) + 1) % ORDER.length];
}

const darkQuery =
	typeof window === 'undefined' ? undefined : window.matchMedia?.('(prefers-color-scheme: dark)');

class Theme {
	#pref = $state<ThemePref>(readThemePref());
	#systemDark = $state(darkQuery?.matches ?? false);

	constructor() {
		darkQuery?.addEventListener?.('change', (event) => (this.#systemDark = event.matches));
	}

	get pref(): ThemePref {
		return this.#pref;
	}

	/** Dunkel nach eigener Wahl oder nach `prefers-color-scheme` */
	get dark(): boolean {
		return this.#pref === 'dark' || (this.#pref === 'system' && this.#systemDark);
	}

	set(pref: ThemePref): void {
		this.#pref = pref;
		const root = document.documentElement;
		if (pref === 'system') delete root.dataset.theme;
		else root.dataset.theme = pref;
		try {
			if (pref === 'system') storage()?.removeItem(THEME_KEY);
			else storage()?.setItem(THEME_KEY, pref);
		} catch {
			// privater Modus: die Wahl gilt nur bis zum Neuladen
		}
	}
}

export const theme = new Theme();
