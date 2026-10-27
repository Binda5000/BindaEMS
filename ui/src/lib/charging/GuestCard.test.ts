import { cleanup, fireEvent, render, screen, within } from '@testing-library/svelte';
import type { ComponentProps } from 'svelte';
import { afterEach, expect, it, vi } from 'vitest';
import GuestCard from './GuestCard.svelte';

type Props = ComponentProps<typeof GuestCard>;

const SETUP = { max_a: null, range: { min_a: 6, max_a: 16, phases: 3 }, voltage: 230 };

function show(changes: Partial<Props> = {}) {
	const props: Props = {
		mode: 'now',
		operate: true,
		busy: false,
		notice: null,
		onMode: vi.fn().mockResolvedValue(undefined),
		nowLimit: SETUP,
		onNowLimit: vi.fn().mockResolvedValue(undefined),
		...changes
	};
	render(GuestCard, props);
	return props;
}

function limitSelect(): HTMLElement {
	return screen.getByRole('combobox', { name: 'Höchststrom' });
}

afterEach(cleanup);

it('bietet in „Sofort“ den Strombereich der EVCS mit den Phasen der EVCS', () => {
	show();
	expect(limitSelect()).toHaveValue('full');
	const options = within(limitSelect()).getAllByRole('option');
	expect(options).toHaveLength(11);
	expect(options[0]).toHaveTextContent('Volle Leistung (16 A · 11,0 kW)');
	expect(options.at(-1)).toHaveTextContent('6 A · 4,1 kW');
});

it('ruft onNowLimit mit der Wahl und mit null für „Volle Leistung“', async () => {
	const props = show({ nowLimit: { ...SETUP, max_a: 8 } });
	expect(limitSelect()).toHaveValue('8');
	await fireEvent.change(limitSelect(), { target: { value: '12' } });
	expect(props.onNowLimit).toHaveBeenLastCalledWith(12);
	await fireEvent.change(limitSelect(), { target: { value: 'full' } });
	expect(props.onNowLimit).toHaveBeenLastCalledWith(null);
});

it('zeigt die Auswahl nur in „Sofort“, nur ab „Bedienen“ und nur mit EVCS', () => {
	show({ mode: 'pv' });
	expect(screen.queryByRole('combobox', { name: 'Höchststrom' })).toBeNull();
	cleanup();
	show({ operate: false });
	expect(screen.queryByRole('combobox', { name: 'Höchststrom' })).toBeNull();
	cleanup();
	show({ nowLimit: null });
	expect(screen.queryByRole('combobox', { name: 'Höchststrom' })).toBeNull();
});

it('sperrt die Auswahl, solange die Karte beschäftigt ist', () => {
	show({ busy: true });
	expect(limitSelect()).toBeDisabled();
});

it('nennt das Limit in einem Satz, auch Lesenden', () => {
	show({ operate: false, nowLimit: { ...SETUP, max_a: 10 } });
	expect(screen.getByText('Sofort mit höchstens 10 A (6,9 kW)')).toBeVisible();
});

it('zeigt einen Fehler der Karte', () => {
	show({ notice: { level: 'error', text: 'Sofort-Höchststrom „3“ liegt außerhalb von 6–16 A.' } });
	expect(screen.getByText('Sofort-Höchststrom „3“ liegt außerhalb von 6–16 A.')).toBeVisible();
});
