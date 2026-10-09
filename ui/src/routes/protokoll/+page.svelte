<script lang="ts">
	import { onMount } from 'svelte';
	import { api } from '$lib/api/endpoints';
	import { AUDIT_GROUPS, auditRows, type AuditGroup } from '$lib/audit/view';
	import Card from '$lib/components/Card.svelte';
	import Notice from '$lib/components/Notice.svelte';
	import { Resource } from '$lib/resource.svelte';

	const LIMIT = 200;
	const audit = new Resource((o) => api.audit(LIMIT, o), { intervalMs: 60_000 });
	onMount(() => audit.start());

	let group = $state<AuditGroup>('alle');
	const rows = $derived(audit.data ? auditRows(audit.data, group) : []);
</script>

<div class="audit">
	<Card title="Änderungsprotokoll">
		<div class="groups" role="group" aria-label="Bereich">
			{#each AUDIT_GROUPS as item (item.id)}
				<button
					type="button"
					class:active={group === item.id}
					aria-pressed={group === item.id}
					onclick={() => (group = item.id)}
				>
					{item.label}
				</button>
			{/each}
		</div>
		<p class="hint muted">Die letzten {LIMIT} Einträge, neueste zuerst.</p>
		{#if audit.error}
			<Notice level="error">{audit.error.detail}</Notice>
		{/if}
		{#if audit.data}
			{#if rows.length > 0}
				<div class="scroll">
					<table>
						<thead>
							<tr>
								<th scope="col">Zeit</th>
								<th scope="col">Wer</th>
								<th scope="col">Aktion</th>
								<th scope="col">Ziel</th>
								<th scope="col">Details</th>
							</tr>
						</thead>
						<tbody>
							{#each rows as row (row.id)}
								<tr>
									<td class="nowrap">{row.when}</td>
									<td>{row.who}</td>
									<td>{row.action}</td>
									<td>{row.target}</td>
									<td>
										{#each row.details as line, index (index)}
											<span class="line">{line}</span>
										{/each}
									</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			{:else}
				<p class="muted">Keine Einträge in diesem Bereich.</p>
			{/if}
		{:else if !audit.error}
			<p class="muted">Lädt …</p>
		{/if}
	</Card>
</div>

<style>
	.audit {
		display: grid;
		gap: var(--gap);
		min-width: 0;
	}

	.groups {
		display: flex;
		flex-wrap: wrap;
		gap: 0.5rem;
	}

	.groups .active {
		border-color: var(--accent);
		color: var(--accent);
		font-weight: 600;
	}

	.hint {
		margin: 0.75rem 0;
		font-size: 0.875rem;
	}

	.scroll {
		overflow-x: auto;
	}

	table {
		min-width: max-content;
	}

	td {
		vertical-align: top;
	}

	.nowrap {
		white-space: nowrap;
	}

	.line {
		display: block;
		font-size: 0.875rem;
	}
</style>
