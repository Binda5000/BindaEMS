import { render, screen, within } from '@testing-library/svelte';
import { expect, it } from 'vitest';
import { ApiError } from '$lib/api/errors';
import { derived } from '$lib/testing/fixtures';
import { balance } from './balance';
import BalanceCard from './BalanceCard.svelte';
import type { FlowConsumer } from './flow';

const labels = { evcs: 'EVCS', twc: 'Garage' };
const consumers: FlowConsumer[] = [
	{ id: 'a', name: 'Küche', color: null, powerW: 230 },
	{ id: 'b', name: 'Obergeschoss', color: null, powerW: 600 },
	{ id: 'other', name: 'Sonstiges', color: null, powerW: 342 }
];

function props(overrides: Partial<Parameters<typeof render<typeof BalanceCard>>[1]> = {}) {
	return {
		balance: balance(
			derived({
				pv_total_w: 3230,
				grid_w: 380,
				battery_w: 1000,
				house_load_w: 1230,
				wallbox_w: { evcs: 0, twc: 1380 }
			}),
			labels
		),
		consumers,
		stale: false,
		error: null,
		...overrides
	};
}

it('trägt den Titel und beschriftet Segmente nur ab 15 % Breite', () => {
	render(BalanceCard, props());
	expect(
		screen.getByRole('heading', { name: 'Woher kommt der Strom – wohin geht er?' })
	).toBeInTheDocument();
	const sources = within(screen.getByTestId('balance-sources'));
	// PV 3230 von 3610 = 89 %, Netz 380 = 11 %
	expect(sources.getByText('PV')).toBeInTheDocument();
	expect(sources.queryByText('Netz')).not.toBeInTheDocument();
});

it('nennt in der Liste alle Segmente mit Leistung', () => {
	render(BalanceCard, props());
	const sources = within(screen.getByTestId('balance-sources-list')).getAllByRole('listitem');
	expect(sources.map((item) => item.textContent?.replace(/\s+/g, ' ').trim())).toEqual([
		'PV 3,23 kW',
		'Netz 380 W'
	]);
	const sinks = within(screen.getByTestId('balance-sinks-list')).getAllByRole('listitem');
	expect(sinks.map((item) => item.textContent?.replace(/\s+/g, ' ').trim())).toEqual([
		'Haus 1,23 kW',
		'Akku 1,00 kW',
		'Garage 1,38 kW'
	]);
});

it('zeigt die Summen der Seiten', () => {
	render(BalanceCard, props());
	expect(screen.getByText(/^Quellen · 3,61\skW$/)).toBeInTheDocument();
	expect(screen.getByText(/^Verbrauch · 3,61\skW$/)).toBeInTheDocument();
});

it('nennt die Verbraucher im Haus und verweist auf /verbraucher', () => {
	render(BalanceCard, props());
	expect(
		screen.getByText(/^Im Haus: Küche 230\sW · Obergeschoss 600\sW · Sonstiges 342\sW$/)
	).toBeInTheDocument();
	expect(screen.getByRole('link', { name: 'Verbraucher im Detail' })).toHaveAttribute(
		'href',
		'/verbraucher'
	);
});

it('lässt „Im Haus“ ohne Verbraucher weg', () => {
	render(BalanceCard, props({ consumers: [] }));
	expect(screen.queryByText(/Im Haus/)).not.toBeInTheDocument();
});

it('zeigt ohne Messwerte Striche und den Fehler', () => {
	render(
		BalanceCard,
		props({
			balance: balance(null, labels),
			error: new ApiError(0, 'Keine Verbindung zum Server')
		})
	);
	expect(screen.getByText(/^Quellen · –$/)).toBeInTheDocument();
	expect(screen.getByText('Keine Verbindung zum Server')).toBeInTheDocument();
});
