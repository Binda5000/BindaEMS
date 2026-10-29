/**
 * Svelte-Action: verkleinert die Schrift eines einzeiligen Werts, bis er in seine Breite passt.
 * Ausgangspunkt ist die Größe aus dem CSS; ohne Layout (Breite 0, z. B. in Tests) bleibt sie.
 * Die CSS-Schätzung je Zeichen trifft nicht jede Systemschrift, darum wird nachgemessen.
 */
export function fitWidth(node: HTMLElement, value = '') {
	let current = value;

	function fit() {
		node.style.fontSize = '';
		if (current === '') return;
		const width = node.clientWidth;
		if (width === 0 || node.scrollWidth <= width) return;
		const size = parseFloat(getComputedStyle(node).fontSize);
		node.style.fontSize = `${Math.floor(((size * width) / node.scrollWidth) * 10) / 10}px`;
	}

	fit();
	const observer =
		typeof ResizeObserver === 'undefined' || node.parentElement === null
			? undefined
			: new ResizeObserver(fit);
	if (node.parentElement !== null) observer?.observe(node.parentElement);
	return {
		update(next: string) {
			current = next;
			fit();
		},
		destroy: () => observer?.disconnect()
	};
}
