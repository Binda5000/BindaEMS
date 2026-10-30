import { render, screen } from '@testing-library/svelte';
import { expect, it } from 'vitest';
import { formatSoc } from '$lib/format';
import { batteryView, type BatteryView } from './battery';
import BatteryTile from './BatteryTile.svelte';

/** 09.10., 12:00 in Wien */
const NOW = Date.parse('2026-10-09T10:00:00+00:00');

const view: BatteryView = {
	pct: 55,
	quality: 'ok',
	kwh: 16.5,
	usableKwh: 30,
	reservePct: 20,
	powerW: 1000,
	direction: 'charge',
	atOrBelowReserve: false,
	gridChargeAllowed: false,
	outlook: { trend: 'up', socPct: 90, at: '2026-10-09T13:00:00+00:00' }
};

it('zeigt SOC, Leistung, Energie, Reserve, Netzladen und Plan', () => {
	render(BatteryTile, { view, stale: false, nowMs: NOW });
	expect(screen.getByTestId('battery-soc')).toHaveTextContent(/^55\s%$/);
	expect(screen.getByText('lädt 1,00 kW')).toBeInTheDocument();
	expect(screen.getByText(/^≈ 16,5 von 30\skWh$/)).toBeInTheDocument();
	expect(screen.getByText(/^≈ 10,5\skWh über Reserve$/)).toBeInTheDocument();
	expect(screen.getByText(/^Reserve 20\s%$/)).toBeInTheDocument();
	expect(screen.getByText('Netzladen aus')).toBeInTheDocument();
	expect(screen.getByText(/^Plan: steigt bis 90\s% um 15:00$/)).toBeInTheDocument();
});

it('nennt im Plan „morgen“, wenn das Ziel nach Mitternacht liegt', () => {
	render(BatteryTile, {
		view: { ...view, outlook: { trend: 'down', socPct: 25, at: '2026-10-10T04:00:00+00:00' } },
		stale: false,
		nowMs: NOW
	});
	expect(screen.getByText(/^Plan: sinkt bis 25\s% morgen um 06:00$/)).toBeInTheDocument();
});

it('beschriftet das Batteriesymbol mit Ladestand und Reserve', () => {
	render(BatteryTile, { view, stale: false, nowMs: NOW });
	expect(screen.getByRole('img')).toHaveAccessibleName(
		`Hausakku ${formatSoc(55)}, Reserve ${formatSoc(20)}`
	);
});

it('zeigt „Netzladen erlaubt“ und lässt es bei unbekanntem Wert weg', () => {
	const { unmount } = render(BatteryTile, {
		view: { ...view, gridChargeAllowed: true },
		stale: false,
		nowMs: NOW
	});
	expect(screen.getByText('Netzladen erlaubt')).toBeInTheDocument();
	unmount();
	render(BatteryTile, { view: { ...view, gridChargeAllowed: null }, stale: false, nowMs: NOW });
	expect(screen.queryByText(/Netzladen/)).not.toBeInTheDocument();
});

it('ohne SOC steht ein Strich und „kein Messwert“', () => {
	render(BatteryTile, {
		view: { ...view, pct: null, quality: 'missing', kwh: null, atOrBelowReserve: false },
		stale: false,
		nowMs: NOW
	});
	expect(screen.getByTestId('battery-soc')).toHaveTextContent('–');
	expect(screen.getByText('kein Messwert')).toBeInTheDocument();
	expect(screen.queryByText(/über Reserve/)).not.toBeInTheDocument();
});

it('ohne Plan fehlt die Planzeile', () => {
	render(BatteryTile, { view: { ...view, outlook: null }, stale: false, nowMs: NOW });
	expect(screen.queryByText(/Plan:/)).not.toBeInTheDocument();
});

it('nennt Entladen, Ruhe und unbekannte Leistung', () => {
	const { unmount } = render(BatteryTile, {
		view: { ...view, direction: 'discharge', powerW: 800 },
		stale: false,
		nowMs: NOW
	});
	expect(screen.getByText('entlädt 800 W')).toBeInTheDocument();
	unmount();
	const idle = render(BatteryTile, {
		view: { ...view, direction: 'idle', powerW: 5 },
		stale: false,
		nowMs: NOW
	});
	expect(screen.getByText('ruht')).toBeInTheDocument();
	idle.unmount();
	render(BatteryTile, {
		view: { ...view, direction: 'unknown', powerW: null },
		stale: false,
		nowMs: NOW
	});
	expect(screen.getByTestId('battery-power')).toHaveTextContent('–');
});

it('dämpft und markiert veraltete Werte', () => {
	const { container } = render(BatteryTile, {
		view: { ...view, quality: 'stale' },
		stale: false,
		nowMs: NOW
	});
	expect(screen.getByText('veraltet')).toBeInTheDocument();
	expect(screen.getByText(/^≈ 10,5\skWh über Reserve$/)).toBeInTheDocument();
	expect(container.querySelector('.tile')).toHaveClass('stale');
});

it('dämpft bei veralteter Verbindung', () => {
	const { container } = render(BatteryTile, { view, stale: true, nowMs: NOW });
	expect(container.querySelector('.tile')).toHaveClass('stale');
	expect(screen.queryByText('veraltet')).not.toBeInTheDocument();
});

it('färbt die Füllung bei Reserve gelb', () => {
	const { container } = render(BatteryTile, {
		view: { ...view, pct: 15, atOrBelowReserve: true },
		stale: false,
		nowMs: NOW
	});
	expect(container.querySelector('.fill')).toHaveClass('low');
	expect(screen.getByText('nichts über Reserve')).toBeInTheDocument();
});

it('baut die Ansicht aus dem Zustand', () => {
	const built = batteryView(null, null, null, 0);
	render(BatteryTile, { view: built, stale: false, nowMs: NOW });
	expect(screen.getByText('kein Messwert')).toBeInTheDocument();
});
