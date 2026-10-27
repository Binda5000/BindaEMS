import { fireEvent, render, screen } from '@testing-library/svelte';
import { expect, it } from 'vitest';
import NoticeBar from './NoticeBar.svelte';
import type { Notice } from './notices';

const three: Notice[] = [
	{ level: 'warning', text: 'MQTT noch nicht verbunden' },
	{ level: 'warning', text: 'Tarifbestandteile ohne Wert' },
	{ level: 'info', text: 'Admin ohne Zwei-Faktor' }
];

it('rendert bei leerer Liste nichts', () => {
	const { container } = render(NoticeBar, { notices: [] });
	expect(container.querySelector('section')).toBeNull();
	expect(screen.queryByRole('button')).not.toBeInTheDocument();
});

it('zeigt Anzahl und die ersten zwei Texte, die volle Liste erst nach „Anzeigen“', async () => {
	render(NoticeBar, { notices: three });
	expect(screen.getByText('3 Hinweise')).toBeInTheDocument();
	expect(
		screen.getByText(/MQTT noch nicht verbunden · Tarifbestandteile ohne Wert/)
	).toBeInTheDocument();
	expect(screen.queryByText('Admin ohne Zwei-Faktor')).not.toBeInTheDocument();

	const button = screen.getByRole('button', { name: 'Anzeigen' });
	expect(button).toHaveAttribute('aria-expanded', 'false');
	await fireEvent.click(button);

	expect(screen.getByRole('button', { name: 'Ausblenden' })).toHaveAttribute(
		'aria-expanded',
		'true'
	);
	for (const notice of three) expect(screen.getByText(notice.text)).toBeInTheDocument();
	expect(screen.getAllByRole('listitem')).toHaveLength(3);

	await fireEvent.click(screen.getByRole('button', { name: 'Ausblenden' }));
	expect(screen.queryByText('Admin ohne Zwei-Faktor')).not.toBeInTheDocument();
});

it('sagt bei einem Hinweis „1 Hinweis“', () => {
	render(NoticeBar, { notices: [{ level: 'info', text: 'Nur einer' }] });
	expect(screen.getByText('1 Hinweis')).toBeInTheDocument();
});

it('richtet die Klasse nach dem schwersten Hinweis', () => {
	const { container } = render(NoticeBar, {
		notices: [
			{ level: 'info', text: 'a' },
			{ level: 'error', text: 'b' }
		]
	});
	expect(container.querySelector('.bar')).toHaveClass('error');
});
