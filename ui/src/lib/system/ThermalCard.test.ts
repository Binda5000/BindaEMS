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
	spare_w: null,
	planned: null
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
	expect(screen.getByText('\u22121,0 K (bestätigt)')).toBeTruthy();
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

it('zeigt ohne Werte der Anlage die Grundlast als „–“', () => {
	render(ThermalCard, { thermal: { klima: { ...silent, base_w: 400, spare_w: 500 } } });
	expect(screen.getByText('Grundlast – · freigebbar –')).toBeTruthy();
});

it('zeigt ohne Klimaanlage keine Karte', () => {
	render(ThermalCard, { thermal: {} });
	expect(screen.queryByRole('heading', { name: 'Klimaanlage' })).toBeNull();
});

it('zeigt in der Karte Klimaanlage den Planwert', () => {
	const plan = {
		valid_until: '2026-10-09T12:00:00+00:00',
		slots: [
			{ start: '2026-10-09T08:00:00+00:00', thermal_steps: 1 },
			{ start: '2026-10-09T08:15:00+00:00', thermal_steps: 1 },
			{ start: '2026-10-09T08:30:00+00:00', thermal_steps: 0 }
		]
	};
	const heating = { ...silent, connected: true, mode: 'heizen', running: true, planned: 1 };
	const { unmount } = render(ThermalCard, {
		thermal: { klima: heating },
		plan,
		now: new Date('2026-10-09T08:05:00Z')
	});
	expect(screen.getByText('Plan')).toBeTruthy();
	expect(screen.getByText('+0,5 K bis 10:30')).toBeTruthy();
	unmount();
	render(ThermalCard, { thermal: { klima: { ...heating, planned: null } }, plan });
	expect(screen.getByText('kein Planwert')).toBeTruthy();
});
