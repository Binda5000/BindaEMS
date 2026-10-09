<script lang="ts">
	import type { RuntimeSettings } from '$lib/api/schemas';
	import { DASH, formatCt, formatNumber, NBSP } from '$lib/format';
	import { describeWindow, validityText } from './model';

	interface Props {
		settings: RuntimeSettings;
	}

	let { settings }: Props = $props();
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
				<tr>
					<td>{component.name}</td>
					<td>{component.source === 'spot' ? 'Börse' : 'fest'}</td>
					<td class="num">
						{#if component.source === 'spot'}
							{DASH}
						{:else if component.value_ct === null}
							<span class="missing">fehlt</span>
						{:else}
							{formatCt(component.value_ct)}
						{/if}
					</td>
					<td>{component.vat ? 'ja' : 'nein'}</td>
					<td>{validityText(component)}</td>
					<td>
						{#if component.windows.length > 0}
							{#each component.windows as window, index (index)}
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
