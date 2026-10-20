<script lang="ts">
	import type { ApiError } from '$lib/api/errors';
	import type { Quality } from '$lib/api/schemas';
	import Card from '$lib/components/Card.svelte';
	import Notice from '$lib/components/Notice.svelte';
	import { PERIODS, qualityRows } from './view';

	interface Props {
		data: Quality | undefined;
		error: ApiError | null;
		/** gewählter Zeitraum in Tagen */
		days: number;
		onSelect: (days: number) => void;
	}

	let { data, error, days, onSelect }: Props = $props();

	const rows = $derived(qualityRows(data?.rows ?? []));
	// die Werte gehören noch zum vorigen Zeitraum, bis die Antwort da ist
	const stale = $derived(data !== undefined && data.days !== days);
</script>

<Card title="Prognosegüte">
	<div class="switch" role="group" aria-label="Zeitraum wählen">
		{#each PERIODS as period (period)}
			<button
				type="button"
				class:active={days === period}
				aria-pressed={days === period}
				onclick={() => onSelect(period)}
			>
				{period} Tage
			</button>
		{/each}
	</div>
	{#if error}
		<Notice level="error">{error.detail}</Notice>
	{/if}
	{#if data}
		<div class="scroll" class:stale aria-busy={stale}>
			{#if rows.length > 0}
				<table>
					<thead>
						<tr>
							<th scope="col">Art</th>
							<th scope="col">Vorlauf</th>
							<th scope="col" class="num">Slots</th>
							<th scope="col" class="num">MAE</th>
							<th scope="col" class="num">Bias</th>
							<th scope="col" class="num">rel. MAE</th>
							<th scope="col" class="num">besser als Profil</th>
						</tr>
					</thead>
					<tbody>
						{#each rows as row (row.key)}
							<tr>
								<td>{row.kind}</td>
								<td>{row.horizon}</td>
								<td class="num">{row.slots}</td>
								<td class="num">{row.mae}</td>
								<td class="num">{row.bias}</td>
								<td class="num">{row.rel}</td>
								<td class="num">{row.better}</td>
							</tr>
						{/each}
					</tbody>
				</table>
			{:else}
				<p class="muted">Noch keine Werte im Zeitraum.</p>
			{/if}
		</div>
	{:else if !error}
		<p class="muted">Lädt …</p>
	{/if}
</Card>

<style>
	.switch {
		display: flex;
		flex-wrap: wrap;
		gap: 0.5rem;
		margin-bottom: 0.75rem;
	}

	.switch .active {
		border-color: var(--accent);
		color: var(--accent);
		font-weight: 600;
	}

	.stale {
		opacity: 0.5;
		transition: opacity 0.2s;
	}

	p {
		margin: 0;
	}
</style>
