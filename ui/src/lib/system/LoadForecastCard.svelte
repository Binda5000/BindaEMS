<script lang="ts">
	import type { ApiError } from '$lib/api/errors';
	import type { LoadForecast } from '$lib/api/schemas';
	import Card from '$lib/components/Card.svelte';
	import Notice from '$lib/components/Notice.svelte';
	import StatusDot from '$lib/components/StatusDot.svelte';
	import { DASH, formatDateTime, formatEnergy, formatNumber } from '$lib/format';

	interface Props {
		data: LoadForecast | undefined;
		error: ApiError | null;
	}

	let { data, error }: Props = $props();

	// Der Server liefert heute, morgen und übermorgen in dieser Reihenfolge und rechnet den
	// Tag selbst; die Beschriftung folgt der Stelle, nicht der Uhr des Browsers (Mitternacht).
	const LABELS = ['Heute', 'Morgen', 'Übermorgen'] as const;
</script>

<Card title="Lastprognose">
	{#if error}
		<Notice level="error">{error.detail}</Notice>
	{/if}
	{#if data}
		<p class="status">
			<StatusDot
				state={data.status.ok ? 'ok' : 'error'}
				label={data.status.ok ? 'in Ordnung' : 'gestört'}
			/>
			{data.status.message}
		</p>
		<dl class="facts">
			<dt>Stand</dt>
			<dd>{data.issued_at ? formatDateTime(data.issued_at) : DASH}</dd>
			<dt>Datenbasis</dt>
			<dd>
				{formatNumber(data.basis.ledger, 0)} Viertelstunden aus BindaEMS, {formatNumber(
					data.basis.ha,
					0
				)} aus Home Assistant
			</dd>
			{#each LABELS as label, index (index)}
				<dt>{label}</dt>
				<dd>{formatEnergy(data.days[index]?.kwh)}</dd>
			{/each}
		</dl>
	{/if}
</Card>

<style>
	.status {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		margin: 0 0 0.75rem;
	}
</style>
