// QR-Code für die Authenticator-App und das Geheimnis zum Abtippen
import { renderSVG } from 'uqr';

/** SVG als Daten-URL (die CSP erlaubt `img-src data:`) */
export function qrDataUrl(text: string): string {
	const svg = renderSVG(text, { ecc: 'M', border: 2 });
	return `data:image/svg+xml;utf8,${encodeURIComponent(svg)}`;
}

/** „JBSWY3DPEHPK3PXP“ → „JBSW Y3DP EHPK 3PXP“ */
export function groupSecret(secret: string): string {
	return secret.replace(/\s+/g, '').replace(/(.{4})(?=.)/g, '$1 ');
}
