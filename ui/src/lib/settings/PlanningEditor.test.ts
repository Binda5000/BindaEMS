import { fireEvent, render, screen, within } from '@testing-library/svelte';
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
	expect(
		screen.getByLabelText('Spitze vermeiden ohne Leistungspreis (ct je kW und Monat)')
	).toHaveValue('100');
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

it('bearbeitet den gedachten Satz für die Monatsspitze', async () => {
	const { draft } = renderEditor();
	const input = screen.getByLabelText('Spitze vermeiden ohne Leistungspreis (ct je kW und Monat)');
	expect(
		screen.getByText(
			'Ohne Leistungspreis meidet der Planer neue Monatsspitzen, als kostete jedes kW darüber diesen Satz; der 15-min-Wächter hält das Budget. 0 schaltet es aus.'
		)
	).toBeInTheDocument();
	await fireEvent.input(input, { target: { value: '0' } });
	expect(draft()).toMatchObject({ peakSoftCt: '0' });
});

it('zeigt den Abschnitt Unwetter-Reserve mit den Standardwerten', () => {
	renderEditor();
	const group = within(screen.getByRole('group', { name: 'Unwetter-Reserve' }));
	expect(group.getByRole('checkbox', { name: 'Unwetter-Reserve' })).not.toBeChecked();
	expect(group.getByLabelText('Ziel-SOC (%)')).toHaveValue('40');
	const level = group.getByRole('combobox', { name: 'Warnstufe' });
	expect(level).toHaveDisplayValue('ab orange');
	expect(
		within(level)
			.getAllByRole('option')
			.map((o) => o.textContent)
	).toEqual(['ab gelb', 'ab orange', 'nur rot']);
	const kinds = within(group.getByRole('group', { name: 'Warnarten' }));
	const boxes = kinds.getAllByRole('checkbox');
	expect(boxes.map((box) => box.closest('label')?.textContent?.trim())).toEqual([
		'Sturm',
		'Regen',
		'Schnee',
		'Glatteis',
		'Gewitter',
		'Hitze',
		'Kälte'
	]);
	expect(boxes.filter((box) => (box as HTMLInputElement).checked).length).toBe(2);
	expect(kinds.getByRole('checkbox', { name: 'Sturm' })).toBeChecked();
	expect(kinds.getByRole('checkbox', { name: 'Gewitter' })).toBeChecked();
	expect(
		group.getByText(
			'Während einer Unwetterwarnung von GeoSphere Austria hält der Planer den Akku mindestens auf dem Ziel und lädt ihn vorher dafür auf, zuerst mit PV, sonst mit höchstens 3 kW aus dem Netz, auch bei ausgeschaltetem „Netzladen erlaubt“; die USV-Reserve aus config.yaml bleibt die harte Untergrenze.'
		)
	).toBeInTheDocument();
});

it('bearbeitet Schalter, Ziel, Stufe und Warnarten der Unwetter-Reserve', async () => {
	const { draft } = renderEditor();
	const group = within(screen.getByRole('group', { name: 'Unwetter-Reserve' }));
	await fireEvent.click(group.getByRole('checkbox', { name: 'Unwetter-Reserve' }));
	await fireEvent.input(group.getByLabelText('Ziel-SOC (%)'), { target: { value: '60' } });
	await fireEvent.change(group.getByRole('combobox', { name: 'Warnstufe' }), {
		target: { value: '3' }
	});
	await fireEvent.click(group.getByRole('checkbox', { name: 'Sturm' }));
	await fireEvent.click(group.getByRole('checkbox', { name: 'Hitze' }));
	expect(draft().storm).toEqual({
		enabled: true,
		targetSocPct: '60',
		minLevel: 3,
		kinds: ['gewitter', 'hitze']
	});
});

it('zeigt die Fehler der Unwetter-Reserve an Ziel und Warnarten', () => {
	renderEditor({
		'planning.storm.target_soc_pct': 'Ganze Zahl von 10 bis 100',
		'planning.storm.kinds': 'Mindestens eine Warnart wählen'
	});
	const target = screen.getByLabelText('Ziel-SOC (%)');
	expect(target).toHaveAttribute('aria-invalid', 'true');
	expect(target).toHaveAccessibleDescription('Ganze Zahl von 10 bis 100');
	expect(screen.getByRole('group', { name: 'Warnarten' })).toHaveAccessibleDescription(
		'Mindestens eine Warnart wählen'
	);
});
