import { fireEvent, render, screen } from '@testing-library/svelte';
import type { ComponentProps } from 'svelte';
import { expect, it, vi } from 'vitest';
import SupportCard from './SupportCard.svelte';

type Props = ComponentProps<typeof SupportCard>;

function show(changes: Partial<Props> = {}) {
	const onChange = vi.fn().mockResolvedValue(undefined);
	const props: Props = {
		support: { allowed: true, soc_pct: null },
		effective: { soc_pct: 82, source: 'plan' },
		configured_pct: 70,
		reserve_pct: 20,
		operate: true,
		busy: false,
		notice: null,
		onChange,
		...changes
	};
	render(SupportCard, props);
	return onChange;
}

it('zeigt Schalter, Zahlfeld und die wirksame Schwelle', () => {
	show({ support: { allowed: true, soc_pct: 60 } });
	expect(screen.getByRole('heading', { level: 2, name: 'Akku-Unterstützung' })).toBeVisible();
	expect(screen.getByRole('checkbox', { name: 'Akku-Unterstützung erlaubt' })).toBeChecked();
	expect(screen.getByRole('spinbutton', { name: 'Unterstützungsschwelle' })).toHaveValue(60);
	expect(screen.getByText('Wirksam ab 82 % (Plan).')).toBeVisible();
	expect(screen.getByRole('button', { name: 'Übernehmen' })).toBeEnabled();
});

it('nennt die Schwelle der Konfiguration im leeren Zahlfeld', () => {
	show();
	const field = screen.getByRole('spinbutton', { name: 'Unterstützungsschwelle' });
	expect(field).toHaveValue(null);
	expect(field).toHaveAttribute('placeholder', '70');
});

it.each([
	['intent', 'Einstellung'],
	['config', 'Konfiguration'],
	['plan', 'Plan']
] as const)('nennt die Quelle %s', (source, label) => {
	show({ effective: { soc_pct: 70, source } });
	expect(screen.getByText(`Wirksam ab 70 % (${label}).`)).toBeVisible();
});

it('kennt die wirksame Schwelle ohne Health nicht', () => {
	show({ effective: null });
	expect(screen.getByText('Wirksame Schwelle unbekannt.')).toBeVisible();
});

it('Lesende sehen die Werte ohne Bedienung', () => {
	show({ operate: false, support: { allowed: false, soc_pct: 60 } });
	expect(screen.queryByRole('checkbox')).toBeNull();
	expect(screen.queryByRole('spinbutton')).toBeNull();
	expect(screen.queryByRole('button')).toBeNull();
	expect(screen.getByText('Akku-Unterstützung erlaubt')).toBeVisible();
	expect(screen.getByText('nein')).toBeVisible();
	expect(screen.getByText('60 %')).toBeVisible();
	expect(screen.getByText('Wirksam ab 82 % (Plan).')).toBeVisible();
});

it('Lesende sehen ohne eigene Schwelle die der Konfiguration', () => {
	show({ operate: false });
	expect(screen.getByText('70 % (Konfiguration)')).toBeVisible();
});

it('der Schalter sendet die Erlaubnis und behält die Schwelle', async () => {
	const onChange = show({ support: { allowed: true, soc_pct: 60 } });
	await fireEvent.click(screen.getByRole('checkbox', { name: 'Akku-Unterstützung erlaubt' }));
	expect(onChange).toHaveBeenCalledWith(false, 60);
});

it('Übernehmen sendet die Schwelle und behält die Erlaubnis', async () => {
	const onChange = show({ support: { allowed: false, soc_pct: null } });
	await fireEvent.input(screen.getByRole('spinbutton', { name: 'Unterstützungsschwelle' }), {
		target: { value: '65' }
	});
	await fireEvent.click(screen.getByRole('button', { name: 'Übernehmen' }));
	expect(onChange).toHaveBeenCalledWith(false, 65);
});

it('ein leeres Zahlfeld sendet null', async () => {
	const onChange = show({ support: { allowed: true, soc_pct: 60 } });
	await fireEvent.input(screen.getByRole('spinbutton', { name: 'Unterstützungsschwelle' }), {
		target: { value: '' }
	});
	await fireEvent.click(screen.getByRole('button', { name: 'Übernehmen' }));
	expect(onChange).toHaveBeenCalledWith(true, null);
});

it.each([
	['19', '19'],
	['101', '101'],
	['19.5', '19,5']
])(
	'ein Wert außerhalb von Reserve bis 100 (%s) zeigt einen Hinweis und sendet nichts',
	async (value, shown) => {
		const onChange = show();
		await fireEvent.input(screen.getByRole('spinbutton', { name: 'Unterstützungsschwelle' }), {
			target: { value }
		});
		await fireEvent.click(screen.getByRole('button', { name: 'Übernehmen' }));
		expect(onChange).not.toHaveBeenCalled();
		expect(
			screen.getByText(`Unterstützungsschwelle „${shown}“ liegt außerhalb von 20–100 %.`)
		).toBeVisible();
	}
);

it('nimmt Reserve und 100 an und löscht den Hinweis', async () => {
	const onChange = show();
	const field = screen.getByRole('spinbutton', { name: 'Unterstützungsschwelle' });
	await fireEvent.input(field, { target: { value: '5' } });
	await fireEvent.click(screen.getByRole('button', { name: 'Übernehmen' }));
	expect(screen.getByText(/liegt außerhalb von/)).toBeVisible();
	await fireEvent.input(field, { target: { value: '20' } });
	await fireEvent.click(screen.getByRole('button', { name: 'Übernehmen' }));
	expect(onChange).toHaveBeenLastCalledWith(true, 20);
	expect(screen.queryByText(/liegt außerhalb von/)).toBeNull();
	await fireEvent.input(field, { target: { value: '100' } });
	await fireEvent.click(screen.getByRole('button', { name: 'Übernehmen' }));
	expect(onChange).toHaveBeenLastCalledWith(true, 100);
});

it('sperrt die Bedienung, solange eine Änderung läuft', () => {
	show({ busy: true });
	expect(screen.getByRole('checkbox', { name: 'Akku-Unterstützung erlaubt' })).toBeDisabled();
	expect(screen.getByRole('spinbutton', { name: 'Unterstützungsschwelle' })).toBeDisabled();
	expect(screen.getByRole('button', { name: 'Übernehmen' })).toBeDisabled();
});

it('zeigt die Meldung des core', () => {
	show({ notice: { level: 'error', text: 'Unterstützungsschwelle zu niedrig.' } });
	expect(screen.getByText('Unterstützungsschwelle zu niedrig.')).toBeVisible();
});
