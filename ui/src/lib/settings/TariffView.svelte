<script lang="ts">
	import type { PriceComponent, RuntimeSettings } from '$lib/api/schemas';
	import { DASH, formatCt, formatEur, formatNumber, NBSP } from '$lib/format';
	import { describeWindow, isEnergy, KIND_LABEL, validityText, windowsOf } from './model';

	interface Props {
		settings: RuntimeSettings;
	}

	let { settings }: Props = $props();

	/** Wert netto mit Einheit; `null` = noch nicht eingetragen */
	function valueText(component: PriceComponent): string | null {
		switch (component.kind) {
			case 'energy_per_kwh':
				return component.source === 'spot' || component.value_ct === null
					? null
					: formatCt(component.value_ct);
			case 'feed_in_per_kwh':
				return component.value_ct === null ? null : formatCt(component.value_ct);
			case 'power_per_kw_year': {
				if (component.value_eur === null) return null;
				const rate = (eur: number) => `${formatEur(eur)}/kW/Jahr`;
				const kw = (value: number) =>
					`${formatNumber(value, Number.isInteger(value) ? 0 : 2)}${NBSP}kW`;
				const parts = [rate(component.value_eur)];
				if (component.value_above_eur !== null) {
					parts.push(`über ${kw(component.tier_kw)} ${rate(component.value_above_eur)}`);
				}
				parts.push(`mind. ${kw(component.minimum_kw)}`);
				return parts.join(', ');
			}
			case 'fixed_per_month':
				return component.value_eur === null ? null : `${formatEur(component.value_eur)}/Monat`;
		}
	}
</script>

<div class="scroll">
	<table>
		<thead>
			<tr>
				<th scope="col">Bestandteil</th>
				<th scope="col">Quelle</th>
				<th scope="col" class="num">Wert netto</th>
				<th scope="col">USt</th>
				<th scope="col">Gültig</th>
				<th scope="col">Zeitfenster</th>
			</tr>
		</thead>
		<tbody>
			{#each settings.tariff.components as component (component.id)}
				{@const value = valueText(component)}
				<tr>
					<td>{component.name}</td>
					<td>
						{#if isEnergy(component)}
							{component.source === 'spot' ? 'Börse' : 'fest'}
						{:else}
							{KIND_LABEL[component.kind]}
						{/if}
					</td>
					<td class="num">
						{#if isEnergy(component) && component.source === 'spot'}
							{DASH}
						{:else if value === null}
							<span class="missing">fehlt</span>
						{:else}
							{value}
						{/if}
					</td>
					<td>{component.vat ? 'ja' : 'nein'}</td>
					<td>{validityText(component)}</td>
					<td>
						{#if windowsOf(component).length > 0}
							{#each windowsOf(component) as window, index (index)}
								<span class="window">{describeWindow(window)}</span>
							{/each}
						{:else}
							{DASH}
						{/if}
					</td>
				</tr>
			{/each}
		</tbody>
	</table>
</div>
<dl class="facts">
	<dt>USt-Satz</dt>
	<dd>{formatNumber(settings.tariff.vat_pct, 0)}{NBSP}%</dd>
	<dt>Vergleichspreis (für die Ersparnis)</dt>
	<dd>{formatCt(settings.tariff.fixed_price_gross_ct)} brutto</dd>
</dl>

<style>
	.facts {
		margin: 1rem 0 0;
	}

	.scroll {
		overflow-x: auto;
	}

	table {
		min-width: max-content;
	}

	th {
		font-size: 0.8125rem;
		color: var(--muted);
	}

	.missing {
		color: var(--warn);
		font-weight: 600;
	}

	.window {
		display: block;
		white-space: nowrap;
	}
</style>
