<script lang="ts">
	// Karte „Wärmemodell“ (Plan 5c): je Betriebsart Grundleistung, Zeitkonstante, Speicher und ob
	// das Modell taugt. Die Karte fehlt ohne Klimaanlage (404) und bis zur ersten Antwort.
	import type { ApiError } from '$lib/api/errors';
	import type { ThermalStatus } from '$lib/api/schemas';
	import Card from '$lib/components/Card.svelte';
	import Notice from '$lib/components/Notice.svelte';
	import StatusDot from '$lib/components/StatusDot.svelte';
	import { computedText, thermalModeView } from './thermal';

	interface Props {
		data: ThermalStatus | undefined;
		error: ApiError | null;
		/** Taste „Jetzt neu berechnen“ nur für Admins */
		admin: boolean;
		/** Der Start läuft gerade (zwischen Klick und Antwort) */
		starting?: boolean;
		/** Meldung zur Taste (Fehler beim Start) */
		message?: string | null;
		onRecompute: () => void;
	}

	let { data, error, admin, starting = false, message = null, onRecompute }: Props = $props();

	const running = $derived(starting || (data?.running ?? false));
	const missing = $derived(error?.status === 404);
	const modes = $derived(data?.modes.map(thermalModeView) ?? []);
	const footer = $derived(computedText(data?.computed_at ?? null));
</script>

{#if !missing && (data || error)}
	<Card title="Wärmemodell">
		{#if error}
			<Notice level="error">{error.detail}</Notice>
		{/if}
		{#if data}
			{#if !data.status.ok}
				<p class="status">
					<StatusDot state="error" label="gestört" />
					{data.status.text}
				</p>
			{/if}
			{#if admin}
				<button type="button" disabled={running} onclick={onRecompute}>
					{running ? 'Berechnung läuft …' : 'Jetzt neu berechnen'}
				</button>
			{:else if running}
				<p class="muted">Berechnung läuft …</p>
			{/if}
			{#if message}
				<Notice level="error">{message}</Notice>
			{/if}

			{#each modes as mode (mode.mode)}
				<div class="mode">
					<h3>{mode.head}</h3>
					{#if mode.rows.length > 0}
						<dl class="facts">
							{#each mode.rows as row (row.label)}
								<dt>{row.label}</dt>
								<dd>{row.value}</dd>
							{/each}
						</dl>
					{/if}
					<p class="state">
						<StatusDot
							state={mode.usable ? 'ok' : 'warn'}
							label={mode.usable ? 'taugt' : 'taugt noch nicht'}
						/>
						{mode.state}
					</p>
				</div>
			{/each}
			{#if footer}
				<p class="muted footer">{footer}</p>
			{/if}
		{/if}
	</Card>
{/if}

<style>
	p {
		margin: 0 0 0.5rem;
	}

	.status,
	.state {
		display: flex;
		align-items: center;
		gap: 0.5rem;
	}

	.mode {
		margin-top: 1rem;
	}

	h3 {
		margin: 0 0 0.5rem;
		font-size: 0.9375rem;
		font-weight: 600;
	}

	.facts {
		margin: 0 0 0.5rem;
	}

	dd {
		overflow-wrap: anywhere;
	}

	.state {
		margin: 0;
	}

	.footer {
		margin: 1rem 0 0;
	}
</style>
