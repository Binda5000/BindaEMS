<script lang="ts">
	import type { ApiError } from '$lib/api/errors';
	import type { Backup } from '$lib/api/schemas';
	import Card from '$lib/components/Card.svelte';
	import Notice from '$lib/components/Notice.svelte';
	import StatusDot from '$lib/components/StatusDot.svelte';
	import { DASH, formatDateTime, formatPower, formatSoc, formatTime, NBSP } from '$lib/format';
	import { durationText, hoursText, shortDate, socText } from './backup';

	interface Props {
		data: Backup | undefined;
		error: ApiError | null;
	}

	let { data, error }: Props = $props();

	/** „≈ 712 W (Mittel 28.10.–3.11.), nachts ≈ 551 W“; die Last liegt nur an AC-Out (USV) */
	const loadText = $derived.by(() => {
		if (!data || data.load_w === null) return null;
		const base = `≈ ${formatPower(data.load_w)}`;
		const source =
			data.load_source === 'ausfall'
				? ' (Mittel des Ausfalls)'
				: data.basis
					? ` (Mittel ${shortDate(data.basis.first_day)}–${shortDate(data.basis.last_day)})`
					: '';
		const night =
			data.basis?.night_w == null ? '' : `, nachts ≈ ${formatPower(data.basis.night_w)}`;
		return `${base}${source}${night}`;
	});

	const pingText = $derived.by(() => {
		if (!data) return '';
		const { configured, last_sent, text, ok } = data.ping;
		if (!configured) return 'nicht eingerichtet';
		const state = text ?? (ok ? 'in Ordnung' : 'gestört');
		return last_sent
			? `eingerichtet, zuletzt ${formatTime(last_sent)}: ${state}`
			: `eingerichtet, noch nichts gesendet: ${state}`;
	});
</script>

<Card title="USV und Netzausfälle">
	{#if error}
		<Notice level="error">{error.detail}</Notice>
	{/if}
	{#if data}
		{#if data.outage}
			<Notice level="warning">
				Netzausfall seit {formatDateTime(data.outage.start)}
			</Notice>
		{/if}
		<dl class="facts">
			<dt>Reichweite jetzt</dt>
			<dd>{hoursText(data.now_h) ?? DASH}</dd>
			<dt>Reichweite bis Reserve</dt>
			<dd>{hoursText(data.reserve_h) ?? DASH}</dd>
			<dt>Reichweite nachts</dt>
			<dd>{hoursText(data.night_h) ?? DASH}</dd>
			<dt>Last an AC-Out</dt>
			<dd>{loadText ?? DASH}</dd>
			<dt>Untergrenze</dt>
			<dd>{formatSoc(data.floor_soc_pct)}</dd>
			<dt>Lebenszeichen</dt>
			<dd class="ping">
				<StatusDot
					state={!data.ping.configured ? 'unknown' : data.ping.ok ? 'ok' : 'error'}
					label={!data.ping.configured
						? 'nicht eingerichtet'
						: data.ping.ok
							? 'in Ordnung'
							: 'gestört'}
				/>
				{pingText}
			</dd>
		</dl>
		<p class="note muted">Gerechnet ohne PV, nur Lasten an AC-Out.</p>

		<h3>Letzte Netzausfälle</h3>
		{#if data.outages.length === 0}
			<p class="muted">Noch kein Netzausfall erfasst.</p>
		{:else}
			<div class="scroll">
				<table class="stack">
					<thead>
						<tr>
							<th scope="col">Beginn</th>
							<th scope="col">Dauer</th>
							<th scope="col">Akkustand</th>
							<th scope="col">Last</th>
						</tr>
					</thead>
					<tbody>
						{#each data.outages as outage, index (index)}
							<tr>
								<td data-label="Beginn">{formatDateTime(outage.start)}</td>
								<td data-label="Dauer">
									{durationText(outage.duration_s)}{#if outage.end === null}
										{NBSP}(läuft){:else if outage.end_estimated}
										{NBSP}(Ende geschätzt){/if}
								</td>
								<td data-label="Akkustand"
									>{socText(outage.soc_start, outage.soc_end, outage.soc_min) ?? DASH}</td
								>
								<td data-label="Last"
									>{outage.mean_w === null ? DASH : `≈ ${formatPower(outage.mean_w)}`}</td
								>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		{/if}
	{/if}
</Card>

<style>
	.ping {
		display: flex;
		align-items: center;
		gap: 0.5rem;
	}

	.note {
		margin: 0 0 0.75rem;
	}

	h3 {
		margin: 1rem 0 0.5rem;
		font-size: 0.9375rem;
	}

	p {
		margin: 0;
	}
</style>
