<script lang="ts">
	import { goto, invalidateAll } from '$app/navigation';
	import { page } from '$app/state';
	import PasswordForm from '$lib/account/PasswordForm.svelte';
	import TotpPanel from '$lib/account/TotpPanel.svelte';
	import { api } from '$lib/api/endpoints';
	import type { User } from '$lib/api/schemas';
	import Card from '$lib/components/Card.svelte';
	import Notice from '$lib/components/Notice.svelte';
	import { live } from '$lib/live.svelte';
	import { ROLE_LABELS } from '$lib/roles';

	const user = $derived(page.data.user as User | null);

	async function logout() {
		try {
			await api.logout();
		} catch {
			// Sitzung ohnehin ungültig
		}
		live.stop();
		await goto('/login', { replaceState: true, invalidateAll: true });
	}
</script>

{#if user}
	<div class="account">
		<Card title="Konto">
			<dl class="facts">
				<dt>Benutzername</dt>
				<dd>{user.username}</dd>
				<dt>Rolle</dt>
				<dd>{ROLE_LABELS[user.role]}</dd>
				<dt>Zwei-Faktor-Anmeldung</dt>
				<dd>{user.totp_enabled ? 'aktiv' : 'aus'}</dd>
			</dl>
			{#if user.role === 'admin' && !user.totp_enabled}
				<Notice level="warning">
					Für Admins empfohlen: Zwei-Faktor-Anmeldung (TOTP) einrichten.
				</Notice>
			{/if}
			<p class="logout"><button type="button" onclick={logout}>Abmelden</button></p>
		</Card>

		<Card title="Passwort ändern">
			<PasswordForm change={api.changePassword} />
		</Card>

		<Card title="Zwei-Faktor-Anmeldung (TOTP)">
			<TotpPanel
				enabled={user.totp_enabled}
				setup={api.totpSetup}
				enable={api.totpEnable}
				disable={api.totpDisable}
				onChanged={() => void invalidateAll()}
			/>
		</Card>
	</div>
{/if}

<style>
	.facts {
		margin: 0 0 0.75rem;
	}

	.account {
		display: grid;
		gap: var(--gap);
		grid-template-columns: repeat(auto-fit, minmax(min(100%, 22rem), 1fr));
		align-items: start;
	}

	.logout {
		margin: 0.75rem 0 0;
	}
</style>
