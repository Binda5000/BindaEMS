import { describe, expect, it } from 'vitest';
import { fitWidth } from './fit';

function element(scroll: number, client: number, fontPx: number): HTMLElement {
	const style = document.createElement('style');
	style.textContent = `.fit-${fontPx} { font-size: ${fontPx}px; }`;
	document.head.append(style);
	const node = document.createElement('strong');
	node.className = `fit-${fontPx}`;
	Object.defineProperty(node, 'scrollWidth', { get: () => scroll });
	Object.defineProperty(node, 'clientWidth', { get: () => client });
	document.body.append(node);
	return node;
}

describe('fitWidth', () => {
	it('verkleinert die Schrift, bis der Wert in die Breite passt', () => {
		const node = element(100, 75, 19);
		const action = fitWidth(node, '1.234,56 €');
		expect(parseFloat(node.style.fontSize)).toBeLessThanOrEqual((19 * 75) / 100);
		action.destroy();
	});

	it('lässt einen passenden Wert unverändert', () => {
		const node = element(60, 75, 19);
		const action = fitWidth(node, '1.234,56 €');
		expect(node.style.fontSize).toBe('');
		action.destroy();
	});

	it('tut ohne Layout (Breite 0) nichts', () => {
		const node = element(0, 0, 19);
		const action = fitWidth(node, '1.234,56 €');
		expect(node.style.fontSize).toBe('');
		action.destroy();
	});
});
