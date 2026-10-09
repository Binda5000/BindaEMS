// Rendert static/icon.svg als PNG-Icons (einmal ausführen, Ergebnis einchecken):
//   node scripts/render-icons.mjs
import { readFileSync } from 'node:fs';
import { chromium } from '@playwright/test';

const ACCENT = '#0b7a5a';
const svg = readFileSync('static/icon.svg', 'utf8');
const source = `data:image/svg+xml;base64,${Buffer.from(svg).toString('base64')}`;

// maskierbar: Motiv auf 80 % (sichere Zone), Rest in der Akzentfarbe; Apple: ohne Transparenz
const ICONS = [
	{ file: 'icon-192.png', size: 192, scale: 1, background: null },
	{ file: 'icon-512.png', size: 512, scale: 1, background: null },
	{ file: 'icon-512-maskable.png', size: 512, scale: 0.8, background: ACCENT },
	{ file: 'apple-touch-icon.png', size: 180, scale: 1, background: ACCENT }
];

const browser = await chromium.launch();
try {
	const page = await browser.newPage({ deviceScaleFactor: 1 });
	for (const { file, size, scale, background } of ICONS) {
		await page.setViewportSize({ width: size, height: size });
		const inner = Math.round(size * scale);
		await page.setContent(
			`<html><body style="margin:0;width:${size}px;height:${size}px;display:grid;` +
				`place-items:center;background:${background ?? 'transparent'}">` +
				`<img src="${source}" width="${inner}" height="${inner}"></body></html>`
		);
		await page.waitForFunction(() => document.querySelector('img')?.complete);
		await page.screenshot({ path: `static/icons/${file}`, omitBackground: background === null });
		console.log(`static/icons/${file} (${size} × ${size})`);
	}
} finally {
	await browser.close();
}
