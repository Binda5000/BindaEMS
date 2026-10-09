// Linien-Icons (24 × 24, Strich in currentColor) für Navigation und Energiefluss
import type { IconName } from '../nav';

export type Shape =
	| { d: string; fill?: boolean }
	| { cx: number; cy: number; r: number }
	| { x: number; y: number; width: number; height: number; rx?: number };

export const ICONS: Record<IconName, Shape[]> = {
	overview: [
		{ x: 3, y: 3, width: 7, height: 9, rx: 1 },
		{ x: 14, y: 3, width: 7, height: 5, rx: 1 },
		{ x: 14, y: 12, width: 7, height: 9, rx: 1 },
		{ x: 3, y: 16, width: 7, height: 5, rx: 1 }
	],
	history: [{ d: 'M3 3v18h18' }, { d: 'M7 15l4-5 4 3 5-7' }],
	consumers: [{ d: 'M9 2v6M15 2v6' }, { d: 'M6 8h12v3a6 6 0 0 1-12 0z' }, { d: 'M12 17v5' }],
	system: [
		{ x: 3, y: 4, width: 18, height: 7, rx: 1 },
		{ x: 3, y: 13, width: 18, height: 7, rx: 1 },
		{ d: 'M7 7.5h.01M7 16.5h.01' }
	],
	settings: [
		{ d: 'M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12' },
		{ cx: 16, cy: 6, r: 2 },
		{ cx: 10, cy: 12, r: 2 },
		{ cx: 18, cy: 18, r: 2 }
	],
	users: [
		{ cx: 9, cy: 8, r: 3.5 },
		{ d: 'M2.5 20a6.5 6.5 0 0 1 13 0' },
		{ d: 'M16 4.5a3.5 3.5 0 0 1 0 7M18 14a6 6 0 0 1 3.5 6' }
	],
	audit: [
		{ x: 5, y: 4, width: 14, height: 17, rx: 2 },
		{ d: 'M9 2h6v4H9z' },
		{ d: 'M9 11h6M9 15h6' }
	],
	account: [{ cx: 12, cy: 12, r: 9 }, { cx: 12, cy: 10, r: 3 }, { d: 'M6.5 18a6 6 0 0 1 11 0' }],
	menu: [{ d: 'M4 6h16M4 12h16M4 18h16' }],
	theme: [
		{ cx: 12, cy: 12, r: 9 },
		{ d: 'M12 3a9 9 0 0 1 0 18z', fill: true }
	],
	pv: [
		{ cx: 12, cy: 12, r: 4 },
		{
			d: 'M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4'
		}
	],
	grid: [{ d: 'M12 2L7 22M12 2l5 20M8.5 8h7M7 14h10M9 8l6 6M15 8l-6 6' }],
	battery: [{ x: 2, y: 7, width: 18, height: 10, rx: 2 }, { d: 'M22 11v2M6 10v4M10 10v4' }],
	house: [{ d: 'M3 11l9-8 9 8' }, { d: 'M5 10v10h14V10' }, { d: 'M10 20v-6h4v6' }],
	wallbox: [
		{ x: 4, y: 3, width: 11, height: 18, rx: 2 },
		{ d: 'M15 9h2a2 2 0 0 1 2 2v4a2 2 0 0 0 4 0V8' },
		{ d: 'M10.5 7l-2 4h3l-2 4' }
	],
	car: [{ d: 'M3 16v-3l2-5h14l2 5v3z' }, { cx: 7, cy: 16.5, r: 1.5 }, { cx: 17, cy: 16.5, r: 1.5 }]
};
