import { render, screen } from '@testing-library/svelte';
import { expect, it } from 'vitest';
import type { CoreHealth } from '$lib/api/schemas';
import ThermalCard from './ThermalCard.svelte';

const silent: CoreHealth['thermal'][string] = {
	label: 'Klimaanlage',
	connected: false,
	last_packet_at: null,
	foreign_packets: 0,
	bad_packets: 0,
	mode: 'unbekannt',
	running: null,
	actual_c: null,
	setpoint_c: null,
	base_setpoint_c: null,
	offset_sent_k: null,
	offset_active_k: null,
	wish: 0,
	power_w: null,
	reason: 'thermal_stale',
	hint: 'neutral'
};

it('zeigt ohne Werte „Keine Werte“ und den Grund', () => {
	render(ThermalCard, { thermal: { klima: silent } });
	expect(screen.getByRole('heading', { name: 'Klimaanlage' })).toBeTruthy();
	expect(screen.getByText('Keine Werte')).toBeTruthy();
	expect(screen.getByText('Keine Werte der Klimaanlage, Versatz 0')).toBeTruthy();
	expect(screen.queryByText('Versatz')).toBeNull();
});

it('zeigt mit Werten die Zeilen', () => {
	render(ThermalCard, {
		thermal: {
			klima: {
				...silent,
				connected: true,
				mode: 'kuehlen',
				running: true,
				offset_sent_k: -1,
				offset_active_k: -1,
				reason: 'thermal_more'
			}
		}
	});
	expect(screen.getByText('Werte kommen an')).toBeTruthy();
	expect(screen.getByText('Kühlen')).toBeTruthy();
	expect(screen.getByText('−1,0 K (bestätigt)')).toBeTruthy();
});

it('zeigt ohne Klimaanlage keine Karte', () => {
	render(ThermalCard, { thermal: {} });
	expect(screen.queryByRole('heading', { name: 'Klimaanlage' })).toBeNull();
});
