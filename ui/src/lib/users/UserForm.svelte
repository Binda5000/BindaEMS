<script lang="ts">
	import { ApiError } from '$lib/api/errors';
	import type { NewUser, Role, User } from '$lib/api/schemas';
	import Notice from '$lib/components/Notice.svelte';
	import { ROLE_LABELS } from '$lib/roles';
	import { validateNewUser, type UserDraft } from './form';

	interface Props {
		create: (body: NewUser) => Promise<User>;
		onCreated: (user: User) => void;
	}

	let { create, onCreated }: Props = $props();

	const uid = $props.id();
	const ROLES: Role[] = ['viewer', 'operator', 'admin'];
	let draft = $state<UserDraft>({ username: '', password: '', role: 'viewer' });
	let errors = $state<Partial<Record<keyof UserDraft, string>>>({});
	let message = $state<string | null>(null);
	let busy = $state(false);

	async function submit(event: SubmitEvent) {
		event.preventDefault();
		message = null;
		const result = validateNewUser(draft);
		if ('errors' in result) {
			errors = result.errors;
			return;
		}
		errors = {};
		busy = true;
		try {
			const user = await create(result.body);
			draft = { username: '', password: '', role: 'viewer' };
			onCreated(user);
		} catch (error) {
			message = error instanceof ApiError ? error.detail : 'Unbekannter Fehler';
		} finally {
			busy = false;
		}
	}
</script>

<form class="user-form" onsubmit={submit} novalidate>
	{#if message}
		<Notice level="error">{message}</Notice>
	{/if}
	<div class="field">
		<label for="{uid}-name">Benutzername</label>
		<input
			id="{uid}-name"
			autocomplete="off"
			autocapitalize="none"
			spellcheck="false"
			bind:value={draft.username}
			aria-invalid={errors.username ? 'true' : undefined}
		/>
		{#if errors.username}<p class="error">{errors.username}</p>{/if}
	</div>
	<div class="field">
		<label for="{uid}-password">Passwort</label>
		<input
			id="{uid}-password"
			type="password"
			autocomplete="new-password"
			bind:value={draft.password}
			aria-invalid={errors.password ? 'true' : undefined}
		/>
		{#if errors.password}<p class="error">{errors.password}</p>{/if}
	</div>
	<div class="field">
		<label for="{uid}-role">Rolle</label>
		<select id="{uid}-role" bind:value={draft.role}>
			{#each ROLES as role (role)}
				<option value={role}>{ROLE_LABELS[role]}</option>
			{/each}
		</select>
	</div>
	<p><button type="submit" disabled={busy}>Anlegen</button></p>
</form>

<style>
	.user-form {
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

	.error {
		color: var(--error);
		font-size: 0.875rem;
	}
</style>
