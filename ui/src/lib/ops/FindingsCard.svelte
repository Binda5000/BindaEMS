<script lang="ts">
	// Karte 2 der Seite Betrieb: oben der Tagesbericht über gestern, darunter je Tag die Treffer
	import type { ApiError } from '$lib/api/errors';
	import type { OpsFindings } from '$lib/api/schemas';
	import Card from '$lib/components/Card.svelte';
	import Notice from '$lib/components/Notice.svelte';
	import StatusDot from '$lib/components/StatusDot.svelte';
	import { formatDay } from '$lib/format';
	import { FINDING_DOT, FINDING_LABEL, linkLabel } from './view';

	interface Props {
		findings: OpsFindings | undefined;
		error: ApiError | null;
	}

	let { findings, error }: Props = $props();

	const warnings = $derived(
		(findings?.days ?? []).flatMap((day) => day.findings).filter((item) => item.level === 'warn')
			.length
	);
</script>

<Card
	title="Auffälligkeiten"
	subtitle={findings ? `letzte ${findings.days.length} Tage bis gestern` : undefined}
>
	{#snippet aside()}
		{#if findings}
			<span class="count" class:warn={warnings > 0}>
				{warnings === 1 ? '1 Warnung' : `${warnings} Warnungen`}
			</span>
		{/if}
	{/snippet}
	{#if error}
		<Notice level="error">{error.detail}</Notice>
	{/if}
	{#if findings}
		{#if findings.report}
			<div class="report">
				<h3>Tagesbericht über gestern</h3>
				<p>{findings.report.text}</p>
			</div>
		{/if}
		<ul class="days">
			{#each findings.days as day (day.day)}
				<li class="day">
					<span class="date">{formatDay(day.day)}</span>
					{#if !day.computed}
						<span class="muted">noch nicht gerechnet</span>
					{:else if day.findings.length === 0}
						<span class="muted">keine</span>
					{:else}
						<ul class="findings">
							{#each day.findings as item, index (index)}
								<li>
									<StatusDot state={FINDING_DOT[item.level]} label={FINDING_LABEL[item.level]} />
									<span class="text">
										{item.text}
										{#if item.link}
											<a href={item.link}>{linkLabel(item.link)}</a>
										{/if}
									</span>
								</li>
							{/each}
						</ul>
					{/if}
				</li>
			{/each}
		</ul>
	{:else if !error}
		<p class="muted">Lädt …</p>
	{/if}
</Card>

<style>
	.count {
		color: var(--muted);
		font-size: 0.875rem;
		white-space: nowrap;
	}

	.count.warn {
		color: var(--warn);
		font-weight: 600;
	}

	.report {
		margin: 0 0 1rem;
		padding: 0.5rem 0.75rem;
		border-left: 4px solid var(--accent);
		border-radius: 4px;
		background: var(--surface-2);
	}

	.report h3 {
		margin: 0 0 0.25rem;
		font-size: 0.9375rem;
	}

	.report p {
		margin: 0;
		overflow-wrap: anywhere;
	}

	.days,
	.findings {
		list-style: none;
		margin: 0;
		padding: 0;
	}

	/* je Tag eine Zeile: Datum links, Treffer rechts daneben; am Handy darunter */
	.day {
		display: grid;
		grid-template-columns: 6.5rem minmax(0, 1fr);
		align-items: baseline;
		gap: 0.25rem 0.75rem;
		padding: 0.375rem 0;
	}

	.day + .day {
		border-top: 1px solid var(--border);
	}

	.date {
		font-weight: 600;
		white-space: nowrap;
	}

	.findings {
		display: grid;
		gap: 0.375rem;
	}

	.findings li {
		display: grid;
		grid-template-columns: auto minmax(0, 1fr);
		align-items: baseline;
		gap: 0.5rem;
	}

	.text {
		overflow-wrap: anywhere;
	}

	.text a {
		margin-left: 0.25rem;
		white-space: nowrap;
	}

	@media (max-width: 40rem) {
		.day:has(.findings) {
			grid-template-columns: minmax(0, 1fr);
		}
	}
</style>
