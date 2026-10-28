import { fireEvent, render, screen } from '@testing-library/svelte';
import { expect, it } from 'vitest';
import { settingsCurrent } from '$lib/testing/fixtures';
import PlanningEditorHost from '$lib/testing/PlanningEditorHost.svelte';
import { draftFrom, type PlanningDraft } from './model';

function renderEditor(errors: Record<string, string> = {}) {
	const probe: { draft: PlanningDraft | null } = { draft: null };
	render(PlanningEditorHost, {
		initial: draftFrom(settingsCurrent().settings).planning,
		errors,
		probe
	});
	const draft = () => {
		if (!probe.draft) throw new Error('Entwurf fehlt');
		return probe.draft;
	};
	return { draft };
}

it('is a group named Planung with all fields', () => {
	renderEditor();
	expect(screen.getByRole('group', { name: 'Planung' })).toBeInTheDocument();
	expect(screen.getByRole('checkbox', { name: 'Netzladen erlaubt' })).not.toBeChecked();
	expect(screen.getByRole('checkbox', { name: 'Akku ins Auto, wenn übrig' })).not.toBeChecked();
	expect(screen.getByLabelText('Marge (ct/kWh)')).toHaveValue('2');
	expect(screen.getByLabelText('Roundtrip-Wirkungsgrad (%)')).toHaveValue('88');
	expect(screen.getByLabelText('Verschleiß (ct/kWh)')).toHaveValue('2');
	expect(screen.getByLabelText('Vorsichtsfaktor Restwert')).toHaveValue('0,9');
	expect(screen.getByLabelText('Planträgheit (ct/kWh)')).toHaveValue('0,5');
});

it('edits planning fields', async () => {
	const { draft } = renderEditor();
	await fireEvent.click(screen.getByRole('checkbox', { name: 'Netzladen erlaubt' }));
	await fireEvent.input(screen.getByLabelText('Marge (ct/kWh)'), { target: { value: '2,5' } });
	expect(draft()).toMatchObject({ gridChargeAllowed: true, marginCt: '2,5' });
});

it('shows the error of a field and links it to the input', () => {
	renderEditor({ 'planning.roundtrip_pct': 'Zahl von 50 bis 100' });
	const input = screen.getByLabelText('Roundtrip-Wirkungsgrad (%)');
	expect(input).toHaveAttribute('aria-invalid', 'true');
	expect(input).toHaveAccessibleDescription('Zahl von 50 bis 100');
});

it('schaltet Akku ins Auto und zeigt den Hilfetext', async () => {
	const { draft } = renderEditor();
	expect(
		screen.getByText(
			'Autos in Geplant und Sofort laden aus dem Akku, wenn das Haus die Energie bis zur nächsten PV-Füllung nicht braucht und Akkustrom billiger ist als Netzstrom.'
		)
	).toBeInTheDocument();
	await fireEvent.click(screen.getByRole('checkbox', { name: 'Akku ins Auto, wenn übrig' }));
	expect(draft()).toMatchObject({ carBatteryAllowed: true, gridChargeAllowed: false });
});
