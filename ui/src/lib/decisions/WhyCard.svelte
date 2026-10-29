<script lang="ts">
	import type { ApiError } from '$lib/api/errors';
	import type { Decision } from '$lib/api/schemas';
	import Card from '$lib/components/Card.svelte';
	import Notice from '$lib/components/Notice.svelte';
	import { ageText } from '$lib/system/view';
	import { decisionAgeS, isWarnResult, resultLabel, whyBody } from './view';

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
				{@const name = names[decision.actuator] ?? decision.actuator}
				<li>
					<a href="/entscheidungen?actuator={encodeURIComponent(decision.actuator)}">
						<span class="head">
							<span class="name">{name}</span>
							<span class="age muted">{ageText(decisionAgeS(decision.ts, nowMs))}</span>
						</span>
						<span class="body">{whyBody(decision.text, name)}</span>
						<span class="result" class:warn={isWarnResult(decision.result)}
							>{resultLabel(decision.result)}</span
						>
					</a>
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
	}

	/* Trennlinien statt Abstand */
	li + li {
		border-top: 1px solid var(--border);
	}

	/* die ganze Zeile ist der Verweis, mindestens 44 px hoch */
	li a {
		display: flex;
		flex-direction: column;
		gap: 0.125rem;
		justify-content: center;
		min-height: 2.75rem;
		padding: 0.5rem 0;
		color: inherit;
		text-decoration: none;
	}

	.head {
		display: flex;
		justify-content: space-between;
		align-items: baseline;
		gap: 0 0.75rem;
	}

	.name {
		min-width: 0;
		font-weight: 600;
		overflow-wrap: anywhere;
	}

	.age {
		flex: none;
		font-size: 0.8125rem;
		white-space: nowrap;
	}

	.body {
		overflow-wrap: anywhere;
	}

	/* Ergebnis als kleines, gedämpftes Etikett */
	.result {
		align-self: flex-start;
		padding: 0 0.5rem;
		border: 1px solid var(--border);
		border-radius: 999px;
		color: var(--muted);
		font-size: 0.8125rem;
	}

	.result.warn {
		border-color: var(--warn);
		color: var(--warn);
	}

	.more {
		margin: 0.5rem 0 0;
	}

	.more a {
		display: inline-flex;
		align-items: center;
		min-height: 2.75rem;
	}
</style>
