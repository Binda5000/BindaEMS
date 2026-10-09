import { expect, it } from 'vitest';
import { groupSecret, qrDataUrl } from './qr';

const PREFIX = 'data:image/svg+xml;utf8,';

it('erzeugt den QR-Code als SVG-Daten-URL', () => {
	const url = qrDataUrl('otpauth://totp/BindaEMS:gast?secret=JBSWY3DPEHPK3PXP&issuer=BindaEMS');
	expect(url.startsWith(PREFIX)).toBe(true);
	expect(decodeURIComponent(url.slice(PREFIX.length))).toContain('<svg');
});

it('gruppiert das Geheimnis in Vierergruppen', () => {
	expect(groupSecret('JBSWY3DPEHPK3PXP')).toBe('JBSW Y3DP EHPK 3PXP');
});
