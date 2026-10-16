<script lang="ts">
	import { api } from '$lib/api/endpoints';
	import { ApiError } from '$lib/api/errors';
	import type { ActuatorMode, ControlReply, CoreHealth, Intent } from '$lib/api/schemas';
	import Card from '$lib/components/Card.svelte';
	import Dialog from '$lib/components/Dialog.svelte';
	import Notice from '$lib/components/Notice.svelte';
	import LiveDialog from './LiveDialog.svelte';

	interface Props {
		health: CoreHealth;
		/** `null`: der core fehlt oder hat noch keine Absicht (Regelbetrieb dann aus) */
		intent: Intent | null;
		operate: boolean;
		admin: boolean;
		/** lädt Absicht und Health nach einer Änderung neu */
		onChanged: () => Promise<void>;
	}

	const DRY_RUN: ActuatorMode = 'dry_run';

	let { health, intent, operate, admin, onChanged }: Props = $props();

	let busy = $state(false);
	let notice = $state<{ level: 'error' | 'warning'; text: string } | null>(null);
	let liveOpen = $state(false);
	let dryRunOpen = $state(false);
	let dryRunError = $state<string | null>(null);

	const regulation = $derived(intent?.control ?? false);
	const liveIds = $derived(health.actuators.filter((a) => a.requested === 'live').map((a) => a.id));

	function failure(error: unknown): string {
		return error instanceof ApiError ? error.detail : 'Unbekannter Fehler';
	}

	/** Eine Begrenzung durch den core steht als Warnung an der Karte. */
	async function changed(reply: ControlReply) {
		if (reply.result === 'clamped') notice = { level: 'warning', text: reply.details.join('\n') };
		await onChanged();
	}

	async function toggleRegulation(event: Event) {
		const input = event.currentTarget as HTMLInputElement;
		const on = input.checked;
		busy = true;
		notice = null;
		try {
			await changed(await api.setRegulation(on));
		} catch (error) {
			notice = { level: 'error', text: failure(error) };
		}
		input.checked = regulation; // bei einer Ablehnung zurück auf den Stand des Servers
		busy = false;
	}

	async function toDryRun() {
		busy = true;
		dryRunError = null;
		notice = null;
		try {
			const modes = Object.fromEntries(liveIds.map((id) => [id, DRY_RUN]));
			const reply = await api.setActuators(modes, null, false);
			dryRunOpen = false;
			await changed(reply);
		} catch (error) {
			dryRunError = failure(error);
		}
		busy = false;
	}
</script>

<Card title="Bedienung">
	{#if operate}
		<label class="switch">
			<input
				type="checkbox"
				role="switch"
				checked={regulation}
				disabled={busy}
				onchange={toggleRegulation}
			/>
			Regelbetrieb
		</label>
	{:else}
		<dl class="facts">
			<dt>Regelbetrieb</dt>
			<dd>{regulation ? 'an' : 'aus'}</dd>
		</dl>
	{/if}

	{#if admin}
		<div class="buttons">
			<button type="button" disabled={busy} onclick={() => (liveOpen = true)}>
				Live schalten…
			</button>
			<button
				type="button"
				disabled={busy || liveIds.length === 0}
				onclick={() => {
					dryRunError = null;
					dryRunOpen = true;
				}}
			>
				Auf Dry-Run…
			</button>
		</div>
	{/if}

	{#if notice}
		<div class="note"><Notice level={notice.level}>{notice.text}</Notice></div>
	{/if}
</Card>

{#if admin}
	<LiveDialog bind:open={liveOpen} {health} {intent} onDone={changed} />

	<Dialog bind:open={dryRunOpen} title="Auf Dry-Run stellen">
		<p>Der core übergibt die gewählten Aktoren und setzt ihre Sicherwerte.</p>
		{#if dryRunError}<p class="error" role="alert">{dryRunError}</p>{/if}
		<div class="buttons">
			<button type="button" class="primary" disabled={busy} onclick={toDryRun}>Auf Dry-Run</button>
			<button type="button" onclick={() => (dryRunOpen = false)}>Abbrechen</button>
		</div>
	</Dialog>
{/if}

<style>
	/* Das Feld ist 44 px hoch, auch wenn der Schalter selbst klein ist */
	.switch {
		display: flex;
		align-items: center;
		gap: 0.625rem;
		min-height: 2.75rem;
		font-weight: 600;
	}

	.switch input {
		flex: none;
	}

	.buttons {
		display: flex;
		flex-wrap: wrap;
		gap: 0.5rem;
		margin-top: 0.75rem;
	}

	.note {
		margin-top: 0.75rem;
	}

	.error {
		color: var(--error);
	}
</style>
