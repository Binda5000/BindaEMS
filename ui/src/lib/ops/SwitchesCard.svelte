<script lang="ts">
	// Karte 3 der Seite Betrieb: alle Schalter je Gruppe mit Stand, ab Werk, seit wann und wer;
	// gestellt wird auf der verlinkten Seite. Am Handy wird jede Zeile zur Karte (table.stack).
	import type { ApiError } from '$lib/api/errors';
	import type { OpsSwitches } from '$lib/api/schemas';
	import Card from '$lib/components/Card.svelte';
	import Notice from '$lib/components/Notice.svelte';
	import { groupSwitches, linkLabel, sinceText } from './view';

	interface Props {
		switches: OpsSwitches | undefined;
		error: ApiError | null;
		now: Date;
	}

	let { switches, error, now }: Props = $props();

	const groups = $derived(groupSwitches(switches?.switches ?? []));
	const changed = $derived((switches?.switches ?? []).filter((item) => item.changed).length);
</script>

<Card title="Schalter" subtitle="stellen kannst du sie auf der verlinkten Seite">
	{#snippet aside()}
		{#if switches}
			<span class="count">{changed} geändert</span>
		{/if}
	{/snippet}
	{#if error}
		<Notice level="error">{error.detail}</Notice>
	{/if}
	{#if switches}
		{#each groups as { group, items } (group)}
			<h3>{group}</h3>
			<div class="scroll">
				<table class="stack" aria-label={group}>
					<thead>
						<tr>
							<th scope="col">Schalter</th>
							<th scope="col">Stand</th>
							<th scope="col">Ab Werk</th>
							<th scope="col">Seit</th>
							<th scope="col">Stellen</th>
						</tr>
					</thead>
					<tbody>
						{#each items as item (item.id)}
							<tr class:changed={item.changed}>
								<th scope="row">{item.label}</th>
								<td data-label="Stand">
									<span class="value">
										{item.value}
										{#if item.changed}<span class="tag">geändert</span>{/if}
									</span>
								</td>
								<td data-label="Ab Werk">{item.factory}</td>
								<td data-label="Seit">{sinceText(item, now)}</td>
								<td data-label="Stellen"><a href={item.link}>{linkLabel(item.link)}</a></td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		{/each}
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

	h3 {
		margin: 1rem 0 0.25rem;
		font-size: 0.9375rem;
	}

	h3:first-of-type {
		margin-top: 0;
	}

	table {
		table-layout: fixed;
	}

	th[scope='col'] {
		font-size: 0.8125rem;
		color: var(--muted);
	}

	th[scope='col']:first-child {
		width: 30%;
	}

	th[scope='row'] {
		font-weight: 600;
	}

	td,
	th[scope='row'] {
		overflow-wrap: anywhere;
		vertical-align: baseline;
	}

	.value {
		display: inline-flex;
		flex-wrap: wrap;
		align-items: baseline;
		gap: 0.25rem 0.5rem;
	}

	tr.changed .value {
		font-weight: 600;
	}

	.tag {
		padding: 0 0.5rem;
		border: 1px solid var(--warn);
		border-radius: 999px;
		color: var(--warn);
		font-size: 0.75rem;
		font-weight: 600;
		white-space: nowrap;
	}

	@media (max-width: 40rem) {
		table {
			table-layout: auto;
		}

		.value {
			justify-content: flex-end;
		}
	}
</style>
