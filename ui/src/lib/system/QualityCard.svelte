<script lang="ts">
	import type { ApiError } from '$lib/api/errors';
	import type { Quality, QualityRow } from '$lib/api/schemas';
	import Card from '$lib/components/Card.svelte';
	import Notice from '$lib/components/Notice.svelte';
	import { DASH, formatNumber, NBSP } from '$lib/format';

	interface Props {
		data: Quality | undefined;
		error: ApiError | null;
	}

	let { data, error }: Props = $props();

	const KINDS: Record<QualityRow['kind'], string> = {
		pv: 'PV',
		load: 'Last (verwendet)',
		load_profile: 'Last Profil',
		load_model: 'Last Modell'
	};
	const HORIZONS: Record<QualityRow['horizon'], string> = {
		h0_6: 'bis 6 h',
		h6_24: '6–24 h',
		h24: 'über 24 h'
	};

	/** Leistung in W ohne Nachkommastelle, auch ab 1000 W (Vergleich der Zeilen) */
	function watts(value: number): string {
		return `${formatNumber(value, 0)}${NBSP}W`;
	}

	function relative(pct: number | null): string {
		return pct === null ? DASH : `${formatNumber(pct, 1)}${NBSP}%`;
	}
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
								<td>{KINDS[row.kind]}</td>
								<td>{HORIZONS[row.horizon]}</td>
								<td class="num">{formatNumber(row.n, 0)}</td>
								<td class="num">{watts(row.mae_w)}</td>
								<td class="num">{watts(row.bias_w)}</td>
								<td class="num">{relative(row.rel_mae_pct)}</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		{:else}
			<p class="muted">Noch keine Kennzahlen.</p>
		{/if}
	{/if}
</Card>

<style>
	p {
		margin: 0 0 0.75rem;
	}
</style>
