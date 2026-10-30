<script lang="ts">
	import { formatSoc } from '$lib/format';
	import {
		batteryLabel,
		energyText,
		gridChargeText,
		outlookText,
		powerText,
		reserveEnergyText,
		type BatteryView
	} from './battery';

	interface Props {
		view: BatteryView;
		/** Live-Verbindung veraltet: alles gedämpft */
		stale: boolean;
		/** „jetzt“ für die Planzeile (heute oder „morgen“) */
		nowMs: number;
	}

	let { view, stale, nowMs }: Props = $props();

	const dimmed = $derived(stale || view.quality === 'stale');
	const fill = $derived(Math.min(100, Math.max(0, view.pct ?? 0)));
	const reserve = $derived(
		view.reservePct === null ? null : Math.min(100, Math.max(0, view.reservePct))
	);
	const energy = $derived(energyText(view));
	const reserveEnergy = $derived(reserveEnergyText(view));
	const gridCharge = $derived(gridChargeText(view.gridChargeAllowed));
</script>

<section class="tile" class:stale={dimmed} aria-label="Hausakku">
	<div class="head">
		<span class="label">Hausakku</span>
		<span class="power" data-testid="battery-power">{powerText(view)}</span>
	</div>

	<div class="main">
		<span class="soc" data-testid="battery-soc">{formatSoc(view.pct)}</span>
		<span class="side">
			{#if view.quality === 'missing'}
				kein Messwert
			{:else}
				{#if energy}<span>{energy}</span>{/if}
				{#if reserveEnergy}<span>{reserveEnergy}</span>{/if}
				{#if view.quality === 'stale'}<span class="tag">veraltet</span>{/if}
			{/if}
		</span>
	</div>

	<div class="battery" role="img" aria-label={batteryLabel(view)}>
		<div class="body">
			<div class="fill" class:low={view.atOrBelowReserve} style:width="{fill}%"></div>
			{#if reserve !== null}
				<div class="reserve" style:left="{reserve}%"></div>
			{/if}
		</div>
		<div class="cap"></div>
	</div>

	<div class="foot">
		{#if view.reservePct !== null}<span>Reserve {formatSoc(view.reservePct)}</span>{/if}
		{#if gridCharge}<span>{gridCharge}</span>{/if}
	</div>
	{#if view.outlook}
		<p class="plan">Plan: {outlookText(view.outlook, nowMs)}</p>
	{/if}
</section>

<style>
	.tile {
		display: flex;
		flex-direction: column;
		gap: 0.625rem;
		min-width: 0;
		padding: 1rem;
		background: var(--surface);
		border: 2px solid var(--battery);
		border-radius: var(--radius);
	}

	.stale {
		opacity: 0.6;
	}

	.head {
		display: flex;
		flex-wrap: wrap;
		justify-content: space-between;
		align-items: baseline;
		gap: 0.25rem 0.5rem;
	}

	.label {
		font-size: 0.8125rem;
		font-weight: 600;
		color: var(--muted);
		text-transform: uppercase;
		letter-spacing: 0.04em;
	}

	.power {
		font-size: 0.875rem;
		font-weight: 600;
		color: var(--battery);
	}

	.main {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.25rem 0.75rem;
	}

	.soc {
		font-size: 2.75rem;
		font-weight: 700;
		line-height: 1;
	}

	.side {
		display: flex;
		flex-direction: column;
		font-size: 0.875rem;
		color: var(--muted);
		line-height: 1.3;
	}

	.tag {
		color: var(--warn);
	}

	.battery {
		display: flex;
		align-items: center;
		gap: 3px;
	}

	.body {
		position: relative;
		flex: 1;
		height: 1.75rem;
		padding: 2px;
		border: 2px solid var(--text);
		border-radius: 6px;
		box-sizing: border-box;
	}

	.fill {
		height: 100%;
		border-radius: 3px;
		background: var(--battery);
	}

	.fill.low {
		background: var(--warn);
	}

	.reserve {
		position: absolute;
		top: -6px;
		bottom: -6px;
		width: 2px;
		background: var(--warn);
	}

	.cap {
		width: 5px;
		height: 12px;
		border-radius: 0 2px 2px 0;
		background: var(--text);
	}

	.foot {
		display: flex;
		flex-wrap: wrap;
		justify-content: space-between;
		gap: 0 0.75rem;
		font-size: 0.8125rem;
		color: var(--muted);
	}

	.plan {
		margin: 0;
		font-size: 0.875rem;
	}
</style>
