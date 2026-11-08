import { fireEvent, render, screen } from '@testing-library/svelte';
import { expect, it, vi } from 'vitest';
import { HUAWEI, SUED, WEST, pvTree, pvWeek, pvYear } from '$lib/testing/pv';
import PvSystemCard from './PvSystemCard.svelte';
import { cards, type PvSystemCard as Card } from './tree';

function weekCard(sources = [HUAWEI, SUED, WEST]): Card {
	const card = cards(pvTree(sources), pvWeek())[0];
	if (!card) throw new Error('keine Karte');
	return card;
}

function show(card: Card, admin = false, onEdit = vi.fn(), onAddString = vi.fn()) {
	return render(PvSystemCard, { card, label: 'Diese Woche', admin, onEdit, onAddString });
}

/** Wert unter der Beschriftung `label` (dt → dd) */
function figure(label: string): string {
	const term = screen.getByText(label, { selector: 'dt' });
	return (term.nextElementSibling?.textContent ?? '').replace(/\s+/g, ' ').trim();
}

it('zeigt Name, kWp und Quelle sowie die drei Kennzahlen', () => {
	show(weekCard());
	expect(screen.getByRole('heading', { name: 'Huawei', level: 2 })).toBeInTheDocument();
	expect(screen.getByText('9,80 kWp · Victron-Zähler (AC)')).toBeInTheDocument();
	expect(figure('Diese Woche')).toBe('49,6 kWh');
	expect(figure('je kWp')).toBe('5,06');
	expect(figure('Jetzt')).toBe('2,96 kW');
});

it('zeigt 0 W als Wert, ohne Wert „–“ und den Grund', () => {
	const card = weekCard();
	show({ ...card, powerW: 0 });
	expect(figure('Jetzt')).toBe('0 W');
	document.body.innerHTML = '';
	show({ ...card, powerW: null, powerNote: 'kein Wert in den letzten 24 h' });
	expect(figure('Jetzt')).toBe('–');
	expect(screen.getByText('kein Wert in den letzten 24 h')).toBeInTheDocument();
});

it('nennt den Grund, wenn die Energie im Zeitraum fehlt, und den ersten Tag mit Wert', () => {
	const card = weekCard();
	show({ ...card, kwh: null, perKwp: null, energyNote: 'Verlauf nicht lesbar' });
	expect(figure('Diese Woche')).toBe('–');
	expect(screen.getByText('Verlauf nicht lesbar')).toBeInTheDocument();
	document.body.innerHTML = '';
	const year = cards(pvTree(), pvYear())[0]!;
	show(year);
	expect(screen.getByText('ab 30.08.')).toBeInTheDocument();
});

it('zeigt Viewern keinen Stift, Admins einen je Anlage und String', async () => {
	show(weekCard());
	expect(screen.queryByRole('button', { name: 'Huawei ändern' })).not.toBeInTheDocument();
	expect(screen.queryByRole('button', { name: 'String hinzufügen' })).not.toBeInTheDocument();
	document.body.innerHTML = '';
	const onEdit = vi.fn();
	const onAddString = vi.fn();
	show(weekCard(), true, onEdit, onAddString);
	await fireEvent.click(screen.getByRole('button', { name: 'Huawei ändern' }));
	await fireEvent.click(screen.getByRole('button', { name: 'West ändern' }));
	expect(onEdit.mock.calls).toEqual([[1], [3]]);
	await fireEvent.click(screen.getByRole('button', { name: 'String hinzufügen' }));
	expect(onAddString).toHaveBeenCalledWith(1);
});

it('vergleicht die Strings je kWp: Süd 5,68 und West 4,88 ergeben 100 % und 86 %', () => {
	const { container } = show(weekCard());
	expect(screen.getByText('Strings im Vergleich (kWh je kWp)')).toBeInTheDocument();
	const rows = [...container.querySelectorAll('.string')].map((row) =>
		(row.querySelector('.values')?.textContent ?? '').replace(/\s+/g, ' ').trim()
	);
	expect(rows).toEqual(['5,68 · 23,3 kWh', '4,88 · 27,8 kWh']);
	const bars = [...container.querySelectorAll<HTMLElement>('.fill')].map((bar) => bar.style.width);
	expect(bars).toEqual(['100%', '86%']);
	expect(screen.getByText('Strings zusammen 51,1 kWh (DC, vor dem Wechselrichter)')).toBeVisible();
});

it('zeigt für einen String ohne kWp nur die kWh und keinen Balken', () => {
	const period = pvWeek();
	period.sources[2]!.kwh_per_kwp = null;
	const card = cards(pvTree([HUAWEI, SUED, { ...WEST, kwp: null }]), period)[0]!;
	const { container } = show(card);
	const west = [...container.querySelectorAll('.string')][1]!;
	expect((west.querySelector('.values')?.textContent ?? '').trim()).toBe('27,8 kWh');
	expect(west.querySelector('.fill')).toBeNull();
	expect(container.querySelectorAll('.fill')).toHaveLength(1);
});

it('zeigt für eine Anlage ohne Strings keinen Vergleich', () => {
	const { container } = show(weekCard([HUAWEI]));
	expect(screen.queryByText(/Strings im Vergleich/)).not.toBeInTheDocument();
	expect(screen.queryByText(/Strings zusammen/)).not.toBeInTheDocument();
	expect(container.querySelector('.string')).toBeNull();
});
