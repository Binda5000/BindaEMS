<script lang="ts">
	import type { ApiError } from '$lib/api/errors';
	import type { Decision } from '$lib/api/schemas';
	import Card from '$lib/components/Card.svelte';
	import Notice from '$lib/components/Notice.svelte';
	import { ageText } from '$lib/system/view';
	import { decisionAgeS } from './view';

	interface Props {
		/** je Aktor die jüngste Entscheidung der letzten 24 h; `undefined`: noch nicht geladen */
		decisions: Decision[] | undefined;
		error: ApiError | null;
		/** Aktor-ID → Anzeigename */
		names: Record<string, string>;
		nowMs: number;
	}

	let { decisions, error, names, nowMs }: Props = $props();
</script>

<Card title="Warum?">
	{#if error}
		<Notice level="error">{error.detail}</Notice>
	{/if}
	{#if decisions === undefined}
		{#if !error}
			<p class="muted">Lädt …</p>
		{/if}
	{:else if decisions.length === 0}
		<p class="muted">Keine Entscheidungen in den letzten 24 h.</p>
	{:else}
		<ul>
			{#each decisions as decision (decision.id)}
				<li>
					<span class="line">
						<a href="/entscheidungen?actuator={encodeURIComponent(decision.actuator)}">
							{names[decision.actuator] ?? decision.actuator}
						</a>
						<span class="sep" aria-hidden="true">·</span>
						<span class="text">{decision.text}</span>
					</span>
					<span class="age muted">{ageText(decisionAgeS(decision.ts, nowMs))}</span>
				</li>
			{/each}
		</ul>
	{/if}
	<p class="more"><a href="/entscheidungen">Alle Entscheidungen</a></p>
</Card>

<style>
	ul {
		list-style: none;
		margin: 0;
		padding: 0;
		display: grid;
		gap: 0.25rem;
	}

	li {
		display: flex;
		flex-wrap: wrap;
		justify-content: space-between;
		align-items: baseline;
		gap: 0 0.75rem;
	}

	.line {
		flex: 1 1 16rem;
		min-width: 0;
		overflow-wrap: anywhere;
	}

	.line a {
		font-weight: 600;
	}

	.age {
		white-space: nowrap;
	}

	/* Verweise mindestens 44 px hoch */
	li a,
	.more a {
		display: inline-flex;
		align-items: center;
		min-height: 2.75rem;
	}

	.more {
		margin: 0.5rem 0 0;
	}
</style>
