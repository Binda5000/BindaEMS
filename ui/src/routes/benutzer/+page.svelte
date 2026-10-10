<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { api } from '$lib/api/endpoints';
	import { ApiError } from '$lib/api/errors';
	import type { Role, User } from '$lib/api/schemas';
	import Card from '$lib/components/Card.svelte';
	import Dialog from '$lib/components/Dialog.svelte';
	import Notice from '$lib/components/Notice.svelte';
	import { formatDateTime } from '$lib/format';
	import { live } from '$lib/live.svelte';
	import { Resource } from '$lib/resource.svelte';
	import { ROLE_LABELS } from '$lib/roles';
	import { MIN_PASSWORD } from '$lib/users/form';
	import UserForm from '$lib/users/UserForm.svelte';

	const ROLES: Role[] = ['viewer', 'operator', 'admin'];
	const users = new Resource((o) => api.users(o));
	onMount(() => users.start());

	const me = $derived((page.data.user as User | null)?.id ?? null);
	let message = $state<{ level: 'info' | 'error'; text: string } | null>(null);

	// Rolle oder Passwort des eigenen Kontos geändert: der Server beendet die Sitzung. Gleich zur
	// Anmeldung mit Grund, statt beim nächsten Laden „Sitzung abgelaufen“ zu melden.
	async function signedOut(reason: 'rolle' | 'passwort') {
		live.stop();
		await goto(`/login?grund=${reason}`, { replaceState: true, invalidateAll: true });
	}

	function failed(error: unknown) {
		message = {
			level: 'error',
			text: error instanceof ApiError ? error.detail : 'Unbekannter Fehler'
		};
	}

	// --- Rolle ---------------------------------------------------------------------------
	let roleChange = $state<{ user: User; role: Role; select: HTMLSelectElement } | null>(null);
	let roleConfirmOpen = $state(false);

	function pickRole(user: User, event: Event & { currentTarget: HTMLSelectElement }) {
		const select = event.currentTarget;
		const role = select.value as Role;
		if (role === user.role) return;
		if (user.id === me) {
			roleChange = { user, role, select }; // eigene Rolle: erst bestätigen
			roleConfirmOpen = true;
		} else {
			void applyRole(user, role, select);
		}
	}

	async function applyRole(user: User, role: Role, select: HTMLSelectElement) {
		message = null;
		try {
			await api.updateUser(user.id, { role });
			if (user.id === me) return await signedOut('rolle');
			message = { level: 'info', text: `Rolle von „${user.username}“: ${ROLE_LABELS[role]}.` };
		} catch (error) {
			select.value = user.role; // abgelehnt: Auswahl zurücksetzen
			failed(error);
		}
		await users.refresh();
	}

	function confirmOwnRole() {
		const change = roleChange;
		roleConfirmOpen = false;
		roleChange = null;
		if (change) void applyRole(change.user, change.role, change.select);
	}

	function cancelOwnRole() {
		if (roleChange) roleChange.select.value = roleChange.user.role;
		roleChange = null;
		roleConfirmOpen = false;
	}

	// auch mit Esc oder „ד geschlossen: ohne Bestätigung bleibt die Rolle
	$effect(() => {
		if (!roleConfirmOpen && roleChange) cancelOwnRole();
	});

	// --- Passwort --------------------------------------------------------------------------
	let passwordFor = $state<User | null>(null);
	let passwordOpen = $state(false);
	let newPassword = $state('');
	let passwordError = $state<string | null>(null);

	function askPassword(user: User) {
		passwordFor = user;
		newPassword = '';
		passwordError = null;
		passwordOpen = true;
	}

	async function setPassword(event: SubmitEvent) {
		event.preventDefault();
		const user = passwordFor;
		if (!user) return;
		if (newPassword.length < MIN_PASSWORD) {
			passwordError = `Mindestens ${MIN_PASSWORD} Zeichen`;
			return;
		}
		try {
			await api.updateUser(user.id, { password: newPassword });
			passwordOpen = false;
			if (user.id === me) return await signedOut('passwort');
			message = {
				level: 'info',
				text: `Passwort für „${user.username}“ gesetzt; bestehende Sitzungen sind abgemeldet.`
			};
		} catch (error) {
			passwordError = error instanceof ApiError ? error.detail : 'Unbekannter Fehler';
		}
	}

	// --- Löschen ---------------------------------------------------------------------------
	let deleting = $state<User | null>(null);
	let deleteOpen = $state(false);

	function askDelete(user: User) {
		deleting = user;
		deleteOpen = true;
	}

	async function confirmDelete() {
		const user = deleting;
		deleteOpen = false;
		if (!user) return;
		message = null;
		try {
			await api.deleteUser(user.id);
			message = { level: 'info', text: `„${user.username}“ gelöscht.` };
		} catch (error) {
			failed(error);
		}
		await users.refresh();
	}

	async function created(user: User) {
		message = { level: 'info', text: `„${user.username}“ angelegt.` };
		await users.refresh();
	}
</script>

<div class="users">
	{#if message}
		<Notice level={message.level}>{message.text}</Notice>
	{/if}

	<Card title="Benutzer">
		{#if users.error}
			<Notice level="error">{users.error.detail}</Notice>
		{/if}
		{#if users.data}
			<div class="scroll">
				<table>
					<thead>
						<tr>
							<th scope="col">Name</th>
							<th scope="col">Rolle</th>
							<th scope="col">TOTP</th>
							<th scope="col">Angelegt</th>
							<th scope="col"><span class="sr-only">Aktionen</span></th>
						</tr>
					</thead>
					<tbody>
						{#each users.data as user (user.id)}
							<tr>
								<td>
									{user.username}
									{#if user.id === me}<span class="muted">(du)</span>{/if}
								</td>
								<td>
									<select
										aria-label="Rolle von {user.username}"
										value={user.role}
										onchange={(event) => pickRole(user, event)}
									>
										{#each ROLES as role (role)}
											<option value={role}>{ROLE_LABELS[role]}</option>
										{/each}
									</select>
								</td>
								<td>{user.totp_enabled ? 'aktiv' : 'aus'}</td>
								<td>{formatDateTime(user.created_at)}</td>
								<td class="actions">
									<button type="button" onclick={() => askPassword(user)}>
										Passwort setzen<span class="sr-only"> für {user.username}</span>
									</button>
									<button type="button" onclick={() => askDelete(user)}>
										Löschen<span class="sr-only"> {user.username}</span>
									</button>
								</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		{:else if !users.error}
			<p class="muted">Lädt …</p>
		{/if}
	</Card>

	<Card title="Benutzer anlegen">
		<UserForm create={api.createUser} onCreated={created} />
	</Card>
</div>

<Dialog bind:open={roleConfirmOpen} title="Eigene Rolle ändern">
	<p>
		Deine Rolle wird „{roleChange ? ROLE_LABELS[roleChange.role] : ''}“. Du wirst danach abgemeldet.
	</p>
	<div class="buttons">
		<button type="button" class="primary" onclick={confirmOwnRole}>Rolle ändern</button>
		<button type="button" onclick={cancelOwnRole}>Abbrechen</button>
	</div>
</Dialog>

<Dialog bind:open={passwordOpen} title="Passwort setzen">
	<form class="password" onsubmit={setPassword} novalidate>
		<p>
			Neues Passwort für „{passwordFor?.username}“. Bestehende Sitzungen werden abgemeldet{passwordFor?.id ===
			me
				? ', auch deine'
				: ''}.
		</p>
		<label for="user-new-password">Neues Passwort</label>
		<input
			id="user-new-password"
			type="password"
			autocomplete="new-password"
			bind:value={newPassword}
		/>
		{#if passwordError}<p class="error">{passwordError}</p>{/if}
		<div class="buttons">
			<button type="submit">Passwort setzen</button>
			<button type="button" onclick={() => (passwordOpen = false)}>Abbrechen</button>
		</div>
	</form>
</Dialog>

<Dialog bind:open={deleteOpen} title="Benutzer löschen">
	<p>„{deleting?.username}“ wirklich löschen?</p>
	<div class="buttons">
		<button type="button" class="danger" onclick={confirmDelete}>Löschen</button>
		<button type="button" onclick={() => (deleteOpen = false)}>Abbrechen</button>
	</div>
</Dialog>

<style>
	.users {
		display: grid;
		gap: var(--gap);
		min-width: 0;
	}

	.scroll {
		overflow-x: auto;
	}

	table {
		min-width: max-content;
	}

	select {
		min-height: 2.25rem;
	}

	.actions {
		text-align: right;
		white-space: nowrap;
	}

	.actions button {
		min-height: 2rem;
		padding: 0.125rem 0.625rem;
		font-size: 0.875rem;
	}

	.actions button + button {
		margin-left: 0.375rem;
	}

	.password {
		display: grid;
		gap: 0.5rem;
	}

	.buttons {
		display: flex;
		gap: 0.5rem;
		margin-top: 0.5rem;
	}

	.error {
		margin: 0;
		color: var(--error);
	}

	.danger {
		border-color: var(--error);
		background: var(--error);
		color: #fff;
	}
</style>
