import { fireEvent, render, screen, within } from '@testing-library/svelte';
import { expect, it } from 'vitest';
import { limits, settingsCurrent } from '$lib/testing/fixtures';
import DrivingEditorHost from '$lib/testing/DrivingEditorHost.svelte';
import { draftFrom, type DrivingDraft } from './model';

function renderEditor(errors: Record<string, string> = {}) {
	const probe: { draft: DrivingDraft | null } = { draft: null };
	const vehicles = limits().vehicles;
	render(DrivingEditorHost, {
		initial: draftFrom(settingsCurrent().settings, [], Object.keys(vehicles)).learning.driving,
		errors,
		vehicles,
		probe
	});
	const draft = () => {
		if (!probe.draft) throw new Error('Entwurf fehlt');
		return probe.draft;
	};
	return { draft };
}

it('zeigt Puffer, Grenzen und Anwesenheit mit den Hilfetexten', () => {
	renderEditor();
	const section = screen.getByRole('group', { name: 'Fahrverhalten' });
	const reserve = within(section).getByLabelText('Puffer bei der Rückkehr (%)');
	expect(reserve).toHaveValue('20');
	expect(reserve).toHaveAccessibleDescription(
		'So viel Ladestand soll nach der typischen Fahrt noch übrig sein.'
	);
	const min = within(section).getByLabelText('Ziel-SOC mindestens (%)');
	const max = within(section).getByLabelText('Ziel-SOC höchstens (%)');
	expect(min).toHaveValue('50');
	expect(max).toHaveValue('90');
	for (const field of [min, max]) {
		expect(field).toHaveAccessibleDescription(
			'Vorschläge und Automatik bleiben in diesem Bereich.'
		);
	}
	// je Fahrzeug ein Feld, mit dem Namen des Autos als Gruppe
	for (const name of ['Tesla Model 3', 'e-Golf']) {
		const car = within(section).getByRole('group', { name });
		const entity = within(car).getByLabelText('Anwesenheit aus Home Assistant');
		expect(entity).toHaveValue('');
		expect(entity).toHaveAccessibleDescription(
			'Optional, etwa device_tracker.… des Autos. Leer: Tessie oder home_entity.'
		);
	}
});

it('trägt Eingaben in den Entwurf ein', async () => {
	const { draft } = renderEditor();
	await fireEvent.input(screen.getByLabelText('Puffer bei der Rückkehr (%)'), {
		target: { value: '30' }
	});
	await fireEvent.input(screen.getByLabelText('Ziel-SOC höchstens (%)'), {
		target: { value: '80' }
	});
	await fireEvent.input(
		within(screen.getByRole('group', { name: 'e-Golf' })).getByLabelText(
			'Anwesenheit aus Home Assistant'
		),
		{ target: { value: 'device_tracker.egolf' } }
	);
	expect(draft().returnReservePct).toBe('30');
	expect(draft().targetMaxPct).toBe('80');
	expect(draft().presenceEntities).toEqual({ tesla: '', egolf: 'device_tracker.egolf' });
});

it('zeigt „Ziel-SOC mindestens liegt über höchstens.“ am Feld', () => {
	renderEditor({ 'learning.driving.target_min_pct': 'Ziel-SOC mindestens liegt über höchstens.' });
	expect(screen.getByText('Ziel-SOC mindestens liegt über höchstens.')).toBeVisible();
	expect(screen.getByLabelText('Ziel-SOC mindestens (%)')).toHaveAttribute('aria-invalid', 'true');
});

it('zeigt auch den Fehler des Servers zum ganzen Abschnitt', () => {
	renderEditor({ 'learning.driving': 'Ziel-SOC mindestens liegt über höchstens.' });
	expect(screen.getByText('Ziel-SOC mindestens liegt über höchstens.')).toBeVisible();
});

it('zeigt Fehler zu Zahl und Entität am Feld', () => {
	renderEditor({
		'learning.driving.return_reserve_pct': 'Ganze Zahl von 0 bis 50',
		'learning.driving.presence_entities.tesla': 'keine gültige Entität'
	});
	expect(screen.getByLabelText('Puffer bei der Rückkehr (%)')).toHaveAccessibleDescription(
		/Ganze Zahl von 0 bis 50/
	);
	expect(screen.getByText('keine gültige Entität')).toBeVisible();
});
