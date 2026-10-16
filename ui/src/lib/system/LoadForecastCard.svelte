<script lang="ts">
	import type { ApiError } from '$lib/api/errors';
	import type { LoadForecast } from '$lib/api/schemas';
	import Card from '$lib/components/Card.svelte';
	import Notice from '$lib/components/Notice.svelte';
	import StatusDot from '$lib/components/StatusDot.svelte';
	import { DASH, formatDateTime, formatEnergy, formatNumber } from '$lib/format';
	import { addDays, todayVienna } from '$lib/time';

	interface Props {
		data: LoadForecast | undefined;
		error: ApiError | null;
		/** heutiger Tag in Wien (`YYYY-MM-DD`); für Tests */
		today?: string;
	}

	let { data, error, today = todayVienna() }: Props = $props();

	const DAYS = [
		['Heute', 0],
		['Morgen', 1],
		['Übermorgen', 2]
	] as const;

	function kwh(date: string): string {
		return formatEnergy(data?.days.find((day) => day.date === date)?.kwh);
	}
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
			{#each DAYS as [label, offset] (offset)}
				<dt>{label}</dt>
				<dd>{kwh(addDays(today, offset))}</dd>
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
