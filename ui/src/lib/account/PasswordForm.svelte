<script lang="ts">
	import { ApiError } from '$lib/api/errors';
	import Notice from '$lib/components/Notice.svelte';

	interface Props {
		change: (oldPassword: string, newPassword: string) => Promise<void>;
	}

	let { change }: Props = $props();

	const MIN_LENGTH = 10; // wie der Server
	const MAX_LENGTH = 1024;
	const uid = $props.id();
	let oldPassword = $state('');
	let newPassword = $state('');
	let repeat = $state('');
	let errors = $state<{ old?: string; new?: string; repeat?: string }>({});
	let result = $state<{ level: 'info' | 'error'; text: string } | null>(null);
	let busy = $state(false);

	async function submit(event: SubmitEvent) {
		event.preventDefault();
		result = null;
		const found: typeof errors = {};
		if (!oldPassword) found.old = 'Altes Passwort fehlt';
		if (newPassword.length < MIN_LENGTH) found.new = `Mindestens ${MIN_LENGTH} Zeichen`;
		else if (newPassword.length > MAX_LENGTH) found.new = `Höchstens ${MAX_LENGTH} Zeichen`;
		else if (repeat !== newPassword) found.repeat = 'Passwörter stimmen nicht überein';
		errors = found;
		if (Object.keys(found).length > 0) return;
		busy = true;
		try {
			await change(oldPassword, newPassword);
			result = { level: 'info', text: 'Passwort geändert. Andere Sitzungen wurden abgemeldet.' };
			oldPassword = newPassword = repeat = '';
		} catch (error) {
			result = {
				level: 'error',
				text: error instanceof ApiError ? error.detail : 'Unbekannter Fehler'
			};
		} finally {
			busy = false;
		}
	}
</script>

<form class="password" onsubmit={submit} novalidate>
	<div class="field">
		<label for="{uid}-old">Altes Passwort</label>
		<input
			id="{uid}-old"
			type="password"
			autocomplete="current-password"
			bind:value={oldPassword}
			aria-invalid={errors.old ? 'true' : undefined}
		/>
		{#if errors.old}<p class="error">{errors.old}</p>{/if}
	</div>
	<div class="field">
		<label for="{uid}-new">Neues Passwort</label>
		<input
			id="{uid}-new"
			type="password"
			autocomplete="new-password"
			bind:value={newPassword}
			aria-invalid={errors.new ? 'true' : undefined}
		/>
		{#if errors.new}<p class="error">{errors.new}</p>{/if}
	</div>
	<div class="field">
		<label for="{uid}-repeat">Neues Passwort wiederholen</label>
		<input
			id="{uid}-repeat"
			type="password"
			autocomplete="new-password"
			bind:value={repeat}
			aria-invalid={errors.repeat ? 'true' : undefined}
		/>
		{#if errors.repeat}<p class="error">{errors.repeat}</p>{/if}
	</div>
	<p class="hint">Mindestens {MIN_LENGTH} Zeichen. Andere Geräte werden danach abgemeldet.</p>
	<p><button type="submit" disabled={busy}>Passwort ändern</button></p>
	{#if result}
		<Notice level={result.level}>{result.text}</Notice>
	{/if}
</form>

<style>
	.password {
		display: grid;
		gap: 0.75rem;
		max-width: 24rem;
	}

	.field {
		display: grid;
		gap: 0.25rem;
	}

	p {
		margin: 0;
	}

	.hint {
		color: var(--muted);
		font-size: 0.875rem;
	}

	.error {
		color: var(--error);
		font-size: 0.875rem;
	}
</style>
