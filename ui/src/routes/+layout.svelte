<script lang="ts">
	import '../app.css';
	import { onMount } from 'svelte';
	import { afterNavigate, goto } from '$app/navigation';
	import { page } from '$app/state';
	import { watchActivity } from '$lib/activity';
	import { onUnauthorized } from '$lib/api/client';
	import { api } from '$lib/api/endpoints';
	import Dialog from '$lib/components/Dialog.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import LiveBadge from '$lib/LiveBadge.svelte';
	import { live } from '$lib/live.svelte';
	import { isCurrent, navItems } from '$lib/nav';
	import { loginHref } from '$lib/navigation';
	import { ROLE_LABELS } from '$lib/roles';
	import { nextThemePref, theme, type ThemePref } from '$lib/theme.svelte';
	import type { LayoutProps } from './$types';

	let { data, children }: LayoutProps = $props();

	const THEME_LABELS: Record<ThemePref, string> = {
		system: 'System',
		light: 'Hell',
		dark: 'Dunkel'
	};

	const user = $derived(data.user);
	const userId = $derived(user?.id ?? null);
	const items = $derived(user ? navItems(user.role) : []);
	const current = $derived(items.find((item) => isCurrent(item.href, page.url.pathname)));
	const title = $derived(current?.label ?? 'BindaEMS');
	// Anmeldeseite ohne Rahmen; sie hat ihre eigene Überschrift
	const bare = $derived(page.url.pathname === '/login' || user === null);
	let menuOpen = $state(false);

	onMount(() => {
		// Sitzung abgelaufen (401 einer Anfrage oder 4401 der Live-Verbindung): zur Anmeldung
		const expired = () => void goto(loginHref(page.url, true), { replaceState: true });
		onUnauthorized(expired);
		live.onUnauthorized = expired;
		return () => {
			onUnauthorized(null);
			live.onUnauthorized = null;
		};
	});

	// Live-Verbindung nur mit Anmeldung; ein neuer Benutzer verbindet neu
	$effect(() => {
		if (userId === null) return;
		live.start();
		return () => live.stop();
	});

	// Bedienung verlängert die Sitzung, auch ohne Anfrage (Tippen in einem Formular)
	$effect(() => {
		if (userId === null) return;
		return watchActivity(window, () => api.me());
	});

	afterNavigate(() => {
		menuOpen = false;
	});

	async function logout() {
		try {
			await api.logout();
		} catch {
			// Sitzung ohnehin ungültig: trotzdem zur Anmeldung
		}
		live.stop();
		await goto('/login', { replaceState: true, invalidateAll: true });
	}
</script>

<svelte:head>
	{#if !bare && !page.error}
		<title>{title} – BindaEMS</title>
	{/if}
</svelte:head>

{#snippet navigation(inMenu: boolean)}
	<nav aria-label="Hauptnavigation">
		<ul>
			{#each items as item (item.href)}
				<li>
					<a
						href={item.href}
						aria-current={isCurrent(item.href, page.url.pathname) ? 'page' : undefined}
					>
						<Icon name={item.icon} />
						{item.label}
					</a>
				</li>
			{/each}
		</ul>
		{#if user}
			<p class="who">{user.username} · {ROLE_LABELS[user.role]}</p>
		{/if}
		{#if inMenu}
			<button type="button" class="menu-logout" onclick={logout}>Abmelden</button>
		{/if}
	</nav>
{/snippet}

{#if bare}
	{@render children()}
{:else}
	<div class="app">
		<a class="skip" href="#inhalt">Zum Inhalt</a>
		<aside class="sidebar">
			<a class="brand" href="/">
				<img src="/favicon.svg" alt="" width="28" height="28" />
				BindaEMS
			</a>
			{@render navigation(false)}
		</aside>

		<div class="main">
			<header class="topbar">
				<button
					type="button"
					class="icon-button menu-button"
					aria-label="Menü"
					onclick={() => (menuOpen = true)}
				>
					<Icon name="menu" />
				</button>
				{#if page.error}
					<span class="title">BindaEMS</span>
				{:else}
					<h1 class="title">{title}</h1>
				{/if}
				<div class="actions">
					<LiveBadge status={live.status} stale={live.stale} coreConnected={live.coreConnected} />
					<button
						type="button"
						class="icon-button"
						aria-label="Darstellung: {THEME_LABELS[theme.pref]}"
						title="Darstellung: {THEME_LABELS[theme.pref]}"
						onclick={() => theme.set(nextThemePref(theme.pref))}
					>
						<Icon name="theme" />
					</button>
					<button type="button" class="logout" onclick={logout}>Abmelden</button>
				</div>
			</header>

			<main id="inhalt">
				{@render children()}
			</main>
		</div>

		<Dialog bind:open={menuOpen} title="Menü">
			{@render navigation(true)}
		</Dialog>
	</div>
{/if}

<style>
	.app {
		min-height: 100dvh;
	}

	.skip {
		position: absolute;
		left: -999px;
		top: 0.5rem;
		z-index: 100;
		padding: 0.5rem 0.75rem;
		background: var(--surface);
		border-radius: 8px;
	}

	.skip:focus {
		left: 0.5rem;
	}

	.sidebar {
		display: none;
	}

	.brand {
		display: flex;
		align-items: center;
		gap: 0.625rem;
		padding: 0.25rem 0.75rem 1rem;
		font-weight: 700;
		font-size: 1.125rem;
		color: var(--text);
		text-decoration: none;
	}

	nav ul {
		list-style: none;
		margin: 0;
		padding: 0;
		display: grid;
		gap: 0.125rem;
	}

	nav a {
		display: flex;
		align-items: center;
		gap: 0.75rem;
		min-height: 2.75rem;
		padding: 0.5rem 0.75rem;
		border-radius: 8px;
		color: var(--text);
		text-decoration: none;
	}

	nav a:hover {
		background: var(--surface-2);
	}

	nav a[aria-current='page'] {
		background: var(--surface-2);
		color: var(--accent);
		font-weight: 600;
	}

	.who {
		margin: 1rem 0 0;
		padding: 0 0.75rem;
		color: var(--muted);
		font-size: 0.875rem;
	}

	.topbar {
		position: sticky;
		top: 0;
		z-index: 10;
		display: flex;
		align-items: center;
		gap: 0.5rem;
		padding: max(0.5rem, env(safe-area-inset-top)) max(var(--gap), env(safe-area-inset-right))
			0.5rem max(var(--gap), env(safe-area-inset-left));
		background: color-mix(in srgb, var(--bg) 92%, transparent);
		backdrop-filter: blur(8px);
		border-bottom: 1px solid var(--border);
	}

	.title {
		flex: 1;
		min-width: 0;
		margin: 0;
		font-size: 1.25rem;
		font-weight: 700;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.actions {
		display: flex;
		align-items: center;
		gap: 0.5rem;
	}

	.icon-button {
		display: inline-grid;
		place-items: center;
		width: 2.5rem;
		padding: 0;
		background: transparent;
		border-color: transparent;
	}

	main {
		width: 100%;
		max-width: 80rem;
		padding: var(--gap) max(var(--gap), env(safe-area-inset-right))
			max(var(--gap), env(safe-area-inset-bottom)) max(var(--gap), env(safe-area-inset-left));
	}

	.menu-logout {
		margin: 1rem 0.75rem 0;
	}

	/* schmal: Abmelden steht im Menü, der Seitentitel braucht den Platz */
	@media (max-width: 40rem) {
		.logout {
			display: none;
		}
	}

	@media (min-width: 960px) {
		.app {
			display: grid;
			grid-template-columns: 15rem minmax(0, 1fr);
		}

		.sidebar {
			display: flex;
			flex-direction: column;
			position: sticky;
			top: 0;
			height: 100dvh;
			overflow-y: auto;
			padding: 1rem 0.75rem;
			background: var(--surface);
			border-right: 1px solid var(--border);
		}

		.menu-button {
			display: none;
		}
	}
</style>
