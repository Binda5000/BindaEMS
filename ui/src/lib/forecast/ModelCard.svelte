<script lang="ts">
	import type { ApiError } from '$lib/api/errors';
	import type { LearningStatus } from '$lib/api/schemas';
	import Card from '$lib/components/Card.svelte';
	import Notice from '$lib/components/Notice.svelte';
	import StatusDot from '$lib/components/StatusDot.svelte';
	import { dataRange, modelStamp } from './view';

	interface Props {
		status: LearningStatus | undefined;
		error: ApiError | null;
		/** Taste „Jetzt neu trainieren“ nur für Admins */
		admin: boolean;
		/** Meldung zur Taste (Fehler beim Start) */
		message?: string | null;
		/** Stufe der Meldung: ein Training, das schon läuft, ist ein Hinweis, kein Fehler */
		messageLevel?: 'error' | 'info';
		/** Der Start läuft gerade (zwischen Klick und Antwort) */
		starting?: boolean;
		onTrain: () => void;
	}

	let {
		status,
		error,
		admin,
		message = null,
		messageLevel = 'error',
		starting = false,
		onTrain
	}: Props = $props();

	const running = $derived(starting || (status?.training.running ?? false));
</script>

<Card title="Lastprognose">
	{#if error}
		<Notice level="error">{error.detail}</Notice>
	{/if}
	{#if status}
		<p class="method">Verfahren: {status.method === 'model' ? 'Modell' : 'Profil'}</p>
		<p>{status.activation.text}</p>
		<p class="status">
			<StatusDot
				state={status.status.ok ? 'ok' : 'error'}
				label={status.status.ok ? 'in Ordnung' : 'gestört'}
			/>
			{status.status.text}
		</p>
		{#if status.model}
			<p>Modell vom {modelStamp(status.model.trained_at)}</p>
			<p class="muted">
				Daten {dataRange(status.model.data_from, status.model.data_to, status.model.samples)}
			</p>
		{:else}
			<p class="muted">Noch kein Modell trainiert.</p>
		{/if}
		{#if status.training.text}
			<p class="muted">{status.training.text}</p>
		{/if}
		{#if admin}
			<button type="button" disabled={running} onclick={onTrain}>
				{running ? 'Training läuft …' : 'Jetzt neu trainieren'}
			</button>
		{:else if running}
			<p class="muted">Training läuft …</p>
		{/if}
		{#if message}
			<Notice level={messageLevel}>{message}</Notice>
		{/if}
	{:else if !error}
		<p class="muted">Lädt …</p>
	{/if}
</Card>

<style>
	p {
		margin: 0 0 0.5rem;
	}

	.method {
		font-weight: 600;
	}

	.status {
		display: flex;
		align-items: center;
		gap: 0.5rem;
	}

	button {
		margin-top: 0.5rem;
	}
</style>
