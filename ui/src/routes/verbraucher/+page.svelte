<script lang="ts">
	import { onMount } from 'svelte';
	import { page } from '$app/state';
	import { api } from '$lib/api/endpoints';
	import { ApiError } from '$lib/api/errors';
	import type { Candidates, ConsumerInput } from '$lib/api/schemas';
	import Card from '$lib/components/Card.svelte';
	import Dialog from '$lib/components/Dialog.svelte';
	import Notice from '$lib/components/Notice.svelte';
	import ConsumerForm from '$lib/consumers/ConsumerForm.svelte';
	import ConsumerTree from '$lib/consumers/ConsumerTree.svelte';
	import { draftFromConsumer, emptyDraft } from '$lib/consumers/form';
	import { consumerRows } from '$lib/consumers/tree';
	import { Resource } from '$lib/resource.svelte';
	import { hasRole } from '$lib/roles';

	const consumers = new Resource((o) => api.consumers(o), { intervalMs: 15_000 });

	// Kandidaten erst beim ersten Öffnen des Formulars laden (InfluxDB kann dauern)
	let candidates = $state.raw<Resource<Candidates> | null>(null);
	let stopCandidates: (() => void) | null = null;

	onMount(() => {
		const stop = consumers.start();
		return () => {
			stop();
			stopCandidates?.();
		};
	});

	const admin = $derived(hasRole(page.data.user, 'admin'));
	const rows = $derived(consumers.data ? consumerRows(consumers.data.tree) : []);
	const list = $derived(consumers.data?.consumers ?? []);

	let formOpen = $state(false);
	let editingId = $state<number | null>(null);
	let draft = $state(emptyDraft());
	let deleting = $state<{ id: number; name: string } | null>(null);
	let deleteOpen = $state(false);
	let message = $state<{ level: 'error' | 'info'; text: string } | null>(null);

	function openForm(id: number | null) {
		const existing = id === null ? undefined : list.find((consumer) => consumer.id === id);
		editingId = existing ? existing.id : null;
		draft = existing ? draftFromConsumer(existing) : emptyDraft();
		if (candidates === null) {
			const resource = new Resource((o) => api.consumerCandidates(o));
			candidates = resource;
			stopCandidates = resource.start();
		}
		message = null;
		formOpen = true;
	}

	async function save(input: ConsumerInput) {
		if (editingId === null) await api.createConsumer(input);
		else await api.updateConsumer(editingId, input);
		formOpen = false;
		message = {
			level: 'info',
			text: `„${input.name}“ ${editingId === null ? 'angelegt' : 'gespeichert'}.`
		};
		await consumers.refresh();
	}

	function askDelete(id: number) {
		const consumer = list.find((item) => item.id === id);
		deleting = { id, name: consumer?.name ?? `Verbraucher ${id}` };
		message = null;
		deleteOpen = true;
	}

	async function confirmDelete() {
		const target = deleting;
		deleteOpen = false;
		if (target === null) return;
		try {
			await api.deleteConsumer(target.id);
			message = { level: 'info', text: `„${target.name}“ gelöscht.` };
		} catch (error) {
			message = {
				level: 'error',
				text: error instanceof ApiError ? error.detail : 'Unbekannter Fehler'
			};
		}
		await consumers.refresh();
	}
</script>

<div class="consumers">
	{#if message}
		<Notice level={message.level}>{message.text}</Notice>
	{/if}

	<Card title="Verbraucherbaum">
		{#if admin}
			<p class="toolbar">
				<button type="button" class="primary" onclick={() => openForm(null)}>
					Verbraucher anlegen
				</button>
			</p>
		{/if}
		{#if consumers.error}
			<Notice level="error">{consumers.error.detail}</Notice>
		{/if}
		{#if rows.length > 0}
			<div class="scroll">
				<ConsumerTree {rows} {admin} onEdit={(id) => openForm(id)} onDelete={askDelete} />
			</div>
			<p class="hint muted">
				„Sonstiges“ ist die Leistung eines Elements abzüglich seiner gemessenen Unterverbraucher.
			</p>
		{:else if !consumers.error}
			<p class="muted">Lädt …</p>
		{/if}
	</Card>
</div>

<Dialog
	bind:open={formOpen}
	title={editingId === null ? 'Verbraucher anlegen' : 'Verbraucher bearbeiten'}
>
	<ConsumerForm
		bind:draft
		consumers={list}
		candidates={candidates?.data}
		{editingId}
		onSave={save}
		onCancel={() => (formOpen = false)}
	/>
</Dialog>

<Dialog bind:open={deleteOpen} title="Verbraucher löschen">
	<p>„{deleting?.name}“ wirklich löschen?</p>
	<div class="buttons">
		<button type="button" class="danger" onclick={confirmDelete}>Löschen</button>
		<button type="button" onclick={() => (deleteOpen = false)}>Abbrechen</button>
	</div>
</Dialog>

<style>
	.consumers {
		display: grid;
		gap: var(--gap);
	}

	.toolbar {
		margin: 0 0 0.75rem;
	}

	.scroll {
		overflow-x: auto;
	}

	.hint {
		margin: 0.75rem 0 0;
		font-size: 0.875rem;
	}

	.buttons {
		display: flex;
		gap: 0.5rem;
	}

	.danger {
		border-color: var(--error);
		background: var(--error);
		color: #fff;
	}
</style>
