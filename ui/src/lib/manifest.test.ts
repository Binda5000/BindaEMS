import { readFileSync } from 'node:fs';
import { expect, it } from 'vitest';

interface Icon {
	src: string;
	sizes: string;
	type: string;
	purpose?: string;
}
const manifest = JSON.parse(readFileSync('static/manifest.webmanifest', 'utf8'));

function pngSize(path: string): string {
	const bytes = readFileSync(path);
	return `${bytes.readUInt32BE(16)}x${bytes.readUInt32BE(20)}`;
}

it('beschreibt eine installierbare App', () => {
	expect(manifest).toMatchObject({
		name: 'BindaEMS',
		short_name: 'BindaEMS',
		lang: 'de',
		start_url: '/',
		scope: '/',
		display: 'standalone'
	});
	const icons: Icon[] = manifest.icons;
	expect(icons.map((icon) => icon.sizes)).toEqual(expect.arrayContaining(['192x192', '512x512']));
	expect(icons.some((icon) => icon.purpose === 'maskable')).toBe(true);
	for (const icon of icons.filter((icon) => icon.type === 'image/png')) {
		expect(pngSize(`static${icon.src}`)).toBe(icon.sizes);
	}
});

it('verlinkt Manifest, Icons und Theme-Farbe in app.html', () => {
	const html = readFileSync('src/app.html', 'utf8');
	for (const part of [
		'rel="manifest"',
		'rel="apple-touch-icon"',
		'rel="icon"',
		'name="theme-color"'
	]) {
		expect(html).toContain(part);
	}
});
