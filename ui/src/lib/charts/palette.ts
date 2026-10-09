// Diagrammfarben aus den CSS-Variablen des aktuellen Themes (hell oder dunkel)

export interface Palette {
	text: string;
	muted: string;
	grid: string;
	importPrice: string;
	feedIn: string;
	pv: string;
	/** Farben für beliebige Reihen (Verlauf) */
	series: string[];
}

export function readPalette(element: Element = document.documentElement): Palette {
	const style = getComputedStyle(element);
	const color = (name: string, fallback: string) => style.getPropertyValue(name).trim() || fallback;
	return {
		text: color('--text', '#1b1f24'),
		muted: color('--muted', '#57606a'),
		grid: color('--border', '#d0d7de'),
		importPrice: color('--battery', '#1f6feb'),
		feedIn: color('--ok', '#1a7f37'),
		pv: color('--pv', '#d18f00'),
		series: [
			color('--grid', '#6e7781'),
			color('--pv', '#d18f00'),
			color('--battery', '#1f6feb'),
			color('--house', '#8250df'),
			color('--wallbox', '#0b7a5a'),
			color('--error', '#c62828'),
			color('--warn', '#9a6700'),
			color('--accent', '#0b7a5a')
		]
	};
}
