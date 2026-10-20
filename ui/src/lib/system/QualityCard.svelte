<script lang="ts">
	import type { ApiError } from '$lib/api/errors';
	import type { Quality } from '$lib/api/schemas';
	import Card from '$lib/components/Card.svelte';
	import Notice from '$lib/components/Notice.svelte';
	import { formatNumber } from '$lib/format';
	import { HORIZON_LABELS, KIND_LABELS, relativeText, watts } from '$lib/forecast/view';

	interface Props {
		data: Quality | undefined;
		error: ApiError | null;
	}

	let { data, error }: Props = $props();
</script>

<Card title="Prognosegüte">
	{#if error}
		<Notice level="error">{error.detail}</Notice>
	{/if}
	{#if data}
		<p class="muted">letzte {data.days} Tage</p>
		{#if data.rows.length > 0}
			<div class="scroll">
				<table>
					<thead>
						<tr>
							<th scope="col">Art</th>
							<th scope="col">Vorlauf</th>
							<th scope="col" class="num">Werte</th>
							<th scope="col" class="num">MAE</th>
							<th scope="col" class="num">Abweichung</th>
							<th scope="col" class="num">MAE relativ</th>
						</tr>
					</thead>
					<tbody>
						{#each data.rows as row (`${row.kind}-${row.horizon}`)}
							<tr>
								<td>{KIND_LABELS[row.kind]}</td>
								<td>{HORIZON_LABELS[row.horizon]}</td>
								<td class="num">{formatNumber(row.n, 0)}</td>
								<td class="num">{watts(row.mae_w)}</td>
								<td class="num">{watts(row.bias_w)}</td>
								<td class="num">{relativeText(row.rel_mae_pct)}</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		{:else}
			<p class="muted">Noch keine Kennzahlen.</p>
		{/if}
	{/if}
	<p class="more"><a href="/prognosen">Mehr unter Prognosen</a></p>
</Card>

<style>
	p {
		margin: 0 0 0.75rem;
	}

	.more {
		margin: 0.75rem 0 0;
	}
</style>
