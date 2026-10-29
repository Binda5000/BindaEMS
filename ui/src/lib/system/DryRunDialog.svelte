<script lang="ts">
	import { api } from '$lib/api/endpoints';
	import { ApiError } from '$lib/api/errors';
	import type { ActuatorMode, ControlReply, CoreHealth } from '$lib/api/schemas';
	import Dialog from '$lib/components/Dialog.svelte';
	import { actuatorDisplay } from '$lib/wallboxes';

	interface Props {
		open: boolean;
		health: CoreHealth;
		/** Schlüssel der Wallbox → Name aus den Einstellungen */
		wallboxNames?: Record<string, string>;
		onDone: (reply: ControlReply) => Promise<void>;
	}

	const DRY_RUN: ActuatorMode = 'dry_run';

	let { open = $bindable(false), health, wallboxNames = {}, onDone }: Props = $props();

	/** Abgewählte Aktoren; jedes Öffnen beginnt mit allen gewählt. */
	let skipped = $state<Record<string, boolean>>({});
	let error = $state<string | null>(null);
	let busy = $state(false);

	$effect(() => {
		void open;
		skipped = {};
		error = null;
		busy = false;
	});

	const liveActuators = $derived(health.actuators.filter((a) => a.requested === 'live'));
	const chosen = $derived(liveActuators.filter((a) => !skipped[a.id]).map((a) => a.id));

	async function submit(event: SubmitEvent) {
		event.preventDefault();
		if (chosen.length === 0 || busy) return;
		busy = true;
		error = null;
		let reply: ControlReply;
		try {
			const modes = Object.fromEntries(chosen.map((id) => [id, DRY_RUN]));
			reply = await api.setActuators(modes, null, false);
		} catch (failure) {
			error = failure instanceof ApiError ? failure.detail : 'Unbekannter Fehler';
			busy = false;
			return;
		}
		busy = false; // erst dann lässt der Dialog sich schließen
		open = false;
		await onDone(reply);
	}
</script>

<Dialog bind:open {busy} title="Auf Dry-Run stellen">
	<form onsubmit={submit} novalidate>
		<p>Der core übergibt die gewählten Aktoren und setzt ihre Sicherwerte.</p>
		<ul>
			{#each liveActuators as actuator (actuator.id)}
				<li>
					<label class="check">
						<input
							type="checkbox"
							checked={!skipped[actuator.id]}
							disabled={busy}
							onchange={(event) => (skipped[actuator.id] = !event.currentTarget.checked)}
						/>
						{actuatorDisplay(actuator.id, actuator.label, wallboxNames)}
					</label>
				</li>
			{/each}
		</ul>
		{#if error}<p class="error" role="alert">{error}</p>{/if}
		<div class="buttons">
			<button type="submit" class="primary" disabled={busy || chosen.length === 0}>
				Auf Dry-Run
			</button>
			<button type="button" disabled={busy} onclick={() => (open = false)}>Abbrechen</button>
		</div>
	</form>
</Dialog>

<style>
	ul {
		list-style: none;
		margin: 0;
		padding: 0;
	}

	p {
		margin-top: 0;
	}

	/* Das Feld ist 44 px hoch, auch wenn das Häkchen selbst klein ist */
	.check {
		display: flex;
		align-items: center;
		gap: 0.625rem;
		min-height: 2.75rem;
	}

	.check input {
		flex: none;
	}

	.error {
		color: var(--error);
	}

	.buttons {
		display: flex;
		flex-wrap: wrap;
		gap: 0.5rem;
		margin-top: 0.5rem;
	}
</style>
