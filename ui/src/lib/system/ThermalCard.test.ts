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
	unit_mismatch: 0,
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
	hint: 'neutral',
	base_w: null,
	spare_w: null
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
	expect(screen.getByText('-1,0 K (bestätigt)')).toBeTruthy();
});

it('zeigt die Grundlast und die freigebbare Leistung, ohne Werte „–“', () => {
	const connected = { ...silent, connected: true, mode: 'kuehlen', running: true };
	const { unmount } = render(ThermalCard, {
		thermal: { klima: { ...connected, base_w: 400, spare_w: 500 } }
	});
	expect(screen.getByText('Grundlast 400 W · freigebbar 500 W')).toBeTruthy();
	unmount();
	render(ThermalCard, { thermal: { klima: connected } });
	expect(screen.getByText('Grundlast – · freigebbar –')).toBeTruthy();
});

it('zeigt ohne Werte der Anlage keine Grundlast', () => {
	render(ThermalCard, { thermal: { klima: silent } });
	expect(screen.queryByText(/Grundlast/)).toBeNull();
});

it('zeigt ohne Klimaanlage keine Karte', () => {
	render(ThermalCard, { thermal: {} });
	expect(screen.queryByRole('heading', { name: 'Klimaanlage' })).toBeNull();
});
