import { render, screen } from '@testing-library/svelte';
import { expect, it } from 'vitest';
import QualityCard from './QualityCard.svelte';

it('zeigt Kennzahlen mit deutschen Bezeichnungen', () => {
	render(QualityCard, {
		data: {
			days: 14,
			rows: [
				{
					kind: 'pv',
					horizon: 'h0_6',
					n: 120,
					mae_w: 412.5,
					bias_w: -80,
					rel_mae_pct: 18.3,
					improvement_pct: null
				}
			]
		},
		error: null
	});
	expect(screen.getByText('PV')).toBeTruthy();
	expect(screen.getByText('bis 6 h')).toBeTruthy();
	expect(screen.getByText('18,3 %')).toBeTruthy();
	expect(screen.getByText('letzte 14 Tage')).toBeTruthy();
	expect(screen.getByText('413 W')).toBeTruthy();
	expect(screen.getByText('-80 W')).toBeTruthy();
	for (const name of ['Art', 'Vorlauf', 'Werte', 'MAE', 'Abweichung', 'MAE relativ']) {
		expect(screen.getByRole('columnheader', { name })).toBeTruthy();
	}
});

it('benennt Last und die anderen Vorläufe, fehlende relative Abweichung ist ein Strich', () => {
	render(QualityCard, {
		data: {
			days: 14,
			rows: [
				{
					kind: 'load',
					horizon: 'h6_24',
					n: 300,
					mae_w: 100,
					bias_w: 20,
					rel_mae_pct: null,
					improvement_pct: null
				},
				{
					kind: 'load',
					horizon: 'h24',
					n: 200,
					mae_w: 150,
					bias_w: 0,
					rel_mae_pct: 12,
					improvement_pct: null
				}
			]
		},
		error: null
	});
	expect(screen.getAllByText('Last (verwendet)')).toHaveLength(2);
	expect(screen.getByText('6 bis 24 h')).toBeTruthy();
	expect(screen.getByText('über 24 h')).toBeTruthy();
	expect(screen.getByText('–')).toBeTruthy();
	expect(screen.getByText('12,0 %')).toBeTruthy();
});

it('zeigt den leeren Zustand', () => {
	render(QualityCard, { data: { days: 14, rows: [] }, error: null });
	expect(screen.getByText('Noch keine Kennzahlen.')).toBeTruthy();
});

it('verweist auf die Seite Prognosen', () => {
	render(QualityCard, { data: { days: 14, rows: [] }, error: null });
	expect(screen.getByRole('link', { name: 'Mehr unter Prognosen' })).toHaveAttribute(
		'href',
		'/prognosen'
	);
});
