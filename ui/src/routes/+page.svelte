<script lang="ts">
	import { onMount } from 'svelte';
	import { api } from '$lib/api/endpoints';
	import EChart from '$lib/charts/EChart.svelte';
	import { priceForecastOption } from '$lib/charts/options';
	import { readPalette } from '$lib/charts/palette';
	import Card from '$lib/components/Card.svelte';
	import Notice from '$lib/components/Notice.svelte';
	import BalanceCard from '$lib/dashboard/BalanceCard.svelte';
	import { balance } from '$lib/dashboard/balance';
	import { batteryView } from '$lib/dashboard/battery';
	import BatteryTile from '$lib/dashboard/BatteryTile.svelte';
	import { flowConsumers, wallboxLabels } from '$lib/dashboard/flow';
	import KpiTile from '$lib/dashboard/KpiTile.svelte';
	import NoticeBar from '$lib/dashboard/NoticeBar.svelte';
	import { noticesFrom } from '$lib/dashboard/notices';
	import PriceTile from '$lib/dashboard/PriceTile.svelte';
	import { forecastForChart } from '$lib/dashboard/prices';
	import {
		dayEnergy,
		dayText,
		gridDayText,
		gridLabel,
		gridPowerText,
		HISTORY_SERIES,
		sparkPoints
	} from '$lib/dashboard/tiles';
	import { vehicleCards } from '$lib/dashboard/vehicles';
	import VehicleTiles from '$lib/dashboard/VehicleTiles.svelte';
	import WhyCard from '$lib/decisions/WhyCard.svelte';
	import { formatPower } from '$lib/format';
	import { live } from '$lib/live.svelte';
	import { Resource } from '$lib/resource.svelte';
	import { theme } from '$lib/theme.svelte';
	import { localDayRange, todayVienna } from '$lib/time';

	const system = new Resource((o) => api.system(o), { intervalMs: 15_000 });
	const consumers = new Resource((o) => api.consumers(o), { intervalMs: 15_000 });
	const limits = new Resource((o) => api.limits(o), { retryMs: 30_000 });
	const pricesNow = new Resource((o) => api.pricesNow(o), { intervalMs: 60_000 });
	const decisions = new Resource((o) => api.latestDecisions(o), { intervalMs: 30_000 });
	const prices = new Resource((o) => api.prices(null, o), { intervalMs: 300_000 });
	const forecast = new Resource((o) => api.forecast(o), { intervalMs: 300_000 });
	const load = new Resource((o) => api.loadForecast(o), { intervalMs: 300_000 });
	const plan = new Resource((o) => api.plan(o), { intervalMs: 60_000 });
	// heute 0 Uhr in Wien bis jetzt, bei jeder Ladung neu (auch über Mitternacht)
	const history = new Resource(
		(o) =>
			api.history(
				[...HISTORY_SERIES],
				{ from: localDayRange(todayVienna()).from, to: new Date().toISOString() },
				o
			),
		{ intervalMs: 120_000 }
	);
	const day = new Resource((o) => api.stats('day', undefined, o), { intervalMs: 300_000 });

	let nowIso = $state(new Date().toISOString()); // „jetzt“ für Diagramm, Plan und Mini-Linien
	const nowMs = $derived(Date.parse(nowIso));

	onMount(() => {
		const stops = [
			system,
			consumers,
			limits,
			pricesNow,
			prices,
			forecast,
			load,
			decisions,
			plan,
			history,
			day
		].map((r) => r.start());
		const clock = setInterval(() => (nowIso = new Date().toISOString()), 60_000);
		return () => {
			stops.forEach((stop) => stop());
			clearInterval(clock);
		};
	});

	const powers = $derived(live.state?.derived); // Leistungen jetzt (Vorzeichen wie im core)
	const wallboxNames = $derived(wallboxLabels(limits.data));
	const notices = $derived(noticesFrom(system.data));
	const battery = $derived(batteryView(live.state, limits.data, plan.data, nowMs));
	const energy = $derived(dayEnergy(day.data));
	const flow = $derived(balance(powers, wallboxNames));
	const houseConsumers = $derived(flowConsumers(consumers.data?.tree));
	// solange die Grenzen der Anlage fehlen, sind die Autos unbekannt („Lädt …“); ohne Gesundheitsbericht
	// (core getrennt, /api/system gescheitert oder noch nicht da) zeigen sie „Stand unbekannt“
	const cars = $derived(
		limits.data === undefined
			? undefined
			: vehicleCards(live.state, limits.data, system.data?.core.health ?? null, wallboxNames)
	);
	const actuatorNames = $derived(
		Object.fromEntries((system.data?.core.health?.actuators ?? []).map((a) => [a.id, a.label]))
	);

	// Farben neu lesen, wenn hell/dunkel wechselt
	const palette = $derived.by(() => {
		void theme.dark;
		return readPalette();
	});

	const forecastSlots = $derived(forecastForChart(forecast.data?.slots ?? [], new Date(nowIso)));

	// ohne Lastprognose (Abfrage gescheitert) zeigt das Diagramm die übrigen Reihen
	const loadChart = $derived(
		load.data ? { ...load.data, slots: forecastForChart(load.data.slots, new Date(nowIso)) } : null
	);

	const chartOption = $derived(
		priceForecastOption(prices.data?.slots ?? [], forecastSlots, loadChart, nowIso, palette, {
			measured: history.data?.series['soc.battery']?.points ?? [],
			plan: plan.data?.plan ?? null
		})
	);
	const chartError = $derived(prices.error ?? forecast.error ?? load.error);
</script>

<div class="dashboard">
	{#if system.error}
		<Notice level="error">{system.error.detail}</Notice>
	{/if}
	<NoticeBar {notices} />

	<div class="tiles">
		<div class="battery">
			<BatteryTile view={battery} stale={live.stale} {nowMs} />
		</div>
		<KpiTile
			data-testid="kpi-pv"
			label="PV"
			value={formatPower(powers?.pv_total_w)}
			color="var(--pv)"
			points={sparkPoints(history.data, 'pv', nowMs)}
			detail={dayText(energy.pvKwh)}
			stale={live.stale}
		/>
		<KpiTile
			data-testid="kpi-house"
			label="Haus"
			value={formatPower(powers?.house_load_w)}
			color="var(--house)"
			points={sparkPoints(history.data, 'house', nowMs)}
			detail={dayText(energy.houseKwh)}
			stale={live.stale}
		/>
		<KpiTile
			data-testid="kpi-grid"
			label={gridLabel(powers?.grid_w)}
			value={gridPowerText(powers?.grid_w)}
			color="var(--grid)"
			points={sparkPoints(history.data, 'grid', nowMs)}
			detail={gridDayText(energy)}
			stale={live.stale}
		/>
		<PriceTile data={pricesNow.data} error={pricesNow.error} />
	</div>

	<div class="pair">
		<BalanceCard
			balance={flow}
			consumers={houseConsumers}
			stale={live.stale}
			error={consumers.error}
		/>
		<VehicleTiles cards={cars} stale={live.stale} error={limits.error} />
	</div>

	<Card title="Preise, PV und Akku heute/morgen">
		{#if chartError}
			<Notice level="error">{chartError.detail}</Notice>
		{/if}
		<EChart
			option={chartOption}
			label="Diagramm: Bezugspreis, Einspeisung, PV-Prognose mit Band, Lastprognose, gemessener und geplanter Akku-SOC für heute und morgen"
		/>
	</Card>

	<WhyCard
		decisions={decisions.data?.decisions}
		error={decisions.error}
		names={actuatorNames}
		{nowMs}
	/>
</div>

<style>
	.dashboard {
		display: grid;
		gap: var(--gap);
		min-width: 0;
	}

	.tiles {
		display: grid;
		gap: 0.75rem;
		grid-template-columns: repeat(auto-fit, minmax(min(100%, 10rem), 1fr));
		min-width: 0;
	}

	.battery {
		display: grid;
		grid-column: 1 / -1;
		min-width: 0;
	}

	.pair {
		display: grid;
		gap: var(--gap);
		align-items: start;
		min-width: 0;
	}

	@media (min-width: 40rem) {
		.battery {
			grid-column: span 2;
		}
	}

	@media (min-width: 60rem) {
		.pair {
			grid-template-columns: repeat(2, minmax(0, 1fr));
		}
	}
</style>
