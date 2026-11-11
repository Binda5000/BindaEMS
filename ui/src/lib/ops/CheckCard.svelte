<script lang="ts">
	// Karte 1 der Seite Betrieb: Gesamtstufe, offene Befunde und die grünen Prüfungen eingeklappt
	import type { ApiError } from '$lib/api/errors';
	import type { OpsCheck, OpsCheckItem } from '$lib/api/schemas';
	import Card from '$lib/components/Card.svelte';
	import Notice from '$lib/components/Notice.svelte';
	import StatusDot from '$lib/components/StatusDot.svelte';
	import { formatTime } from '$lib/format';
	import { checkHeadline, levelLabel, linkLabel, openItems } from './view';

	interface Props {
		check: OpsCheck | undefined;
		error: ApiError | null;
	}

	let { check, error }: Props = $props();

	const open = $derived(openItems(check?.items ?? []));
	const fine = $derived((check?.items ?? []).filter((item) => item.level === 'ok'));
</script>

{#snippet row(item: OpsCheckItem)}
	<li>
		<StatusDot state={item.level} label={levelLabel(item.level)} />
		<span class="label">{item.label}</span>
		<span class="text">
			{item.text}
			{#if item.link}
				<a href={item.link}>{linkLabel(item.link)}</a>
			{/if}
		</span>
	</li>
{/snippet}

<Card
	title="Betriebscheck"
	subtitle={check ? `geprüft ${formatTime(check.checked_at)} Uhr` : undefined}
>
	{#snippet aside()}
		{#if check}
			<span class="headline {check.level}">
				<StatusDot state={check.level} label={levelLabel(check.level)} />
				{checkHeadline(check)}
			</span>
		{/if}
	{/snippet}
	{#if error}
		<Notice level="error">{error.detail}</Notice>
	{/if}
	{#if check}
		{#if open.length > 0}
			<ul class="items" aria-label="Befunde">
				{#each open as item (item.id)}
					{@render row(item)}
				{/each}
			</ul>
		{/if}
		{#if fine.length > 0}
			<details>
				<summary>{fine.length} {fine.length === 1 ? 'Prüfung' : 'Prüfungen'} in Ordnung</summary>
				<ul class="items">
					{#each fine as item (item.id)}
						{@render row(item)}
					{/each}
				</ul>
			</details>
		{/if}
	{:else if !error}
		<p class="muted">Lädt …</p>
	{/if}
</Card>

<style>
	.headline {
		display: inline-flex;
		align-items: center;
		gap: 0.5rem;
		font-weight: 600;
		text-align: right;
	}

	.headline.warn {
		color: var(--warn);
	}

	.headline.error {
		color: var(--error);
	}

	.items {
		list-style: none;
		margin: 0;
		padding: 0;
	}

	/* je Prüfung: Punkt, Name, Satz mit Link; am Handy steht der Satz unter dem Namen */
	.items li {
		display: grid;
		grid-template-columns: auto minmax(8rem, 12rem) minmax(0, 1fr);
		align-items: baseline;
		gap: 0.25rem 0.75rem;
		padding: 0.5rem 0;
	}

	.items li + li {
		border-top: 1px solid var(--border);
	}

	.label {
		font-weight: 600;
	}

	.text {
		overflow-wrap: anywhere;
	}

	.text a {
		margin-left: 0.25rem;
		white-space: nowrap;
	}

	details {
		margin-top: 0.5rem;
	}

	summary {
		cursor: pointer;
		padding: 0.5rem 0;
		color: var(--muted);
	}

	@media (max-width: 40rem) {
		.items li {
			grid-template-columns: auto minmax(0, 1fr);
		}

		.text {
			grid-column: 2;
		}
	}
</style>
