<script lang="ts">
	import Card from '$lib/components/Card.svelte';
	import { DASH, NBSP, formatPower } from '$lib/format';
	import { formatKwh, formatKwp, formatPerKwp, type PvSystemCard } from './tree';

	interface Props {
		card: PvSystemCard;
		/** Beschriftung der Energie im Zeitraum, z. B. „Diese Woche“ */
		label: string;
		admin: boolean;
		onEdit: (id: number) => void;
		onAddString: (systemId: number) => void;
	}

	let { card, label, admin, onEdit, onAddString }: Props = $props();

	const SEPARATOR = ' · '; // zwischen kWh/kWp und kWh (Leerzeichen am Rand eines Blocks fielen weg)

	const subtitle = $derived(
		card.kwp === null ? card.source : `${formatKwp(card.kwp)}${NBSP}kWp · ${card.source}`
	);

	/** „2,96 kW“ → Zahl und Einheit (die Einheit steht kleiner) */
	function parts(text: string): [string, string] {
		const at = text.indexOf(NBSP);
		return at < 0 ? [text, ''] : [text.slice(0, at), text.slice(at)];
	}
</script>

{#snippet pencil(name: string, id: number)}
	<button type="button" class="edit" aria-label="{name} ändern" onclick={() => onEdit(id)}>
		<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 20h4L19 9l-4-4L4 16v4z" /></svg>
	</button>
{/snippet}

{#snippet value(text: string)}
	{@const [number, unit] = parts(text)}
	{number}{#if unit}<span class="unit">{unit}</span>{/if}
{/snippet}

<Card title={card.name} {subtitle}>
	{#snippet aside()}
		{#if admin}{@render pencil(card.name, card.id)}{/if}
	{/snippet}

	<dl class="figures">
		<div>
			<dt>{label}</dt>
			<dd>{@render value(formatKwh(card.kwh))}</dd>
			{#if card.kwh === null && card.energyNote}
				<p class="note">{card.energyNote}</p>
			{:else if card.since}
				<p class="note">{card.since}</p>
			{/if}
		</div>
		<div>
			<dt>je kWp</dt>
			<dd>{formatPerKwp(card.perKwp)}</dd>
			{#if card.kwp === null}
				<p class="note">ohne kWp</p>
			{/if}
		</div>
		<div>
			<dt>Jetzt</dt>
			<dd>{@render value(formatPower(card.powerW))}</dd>
			{#if card.powerW === null && card.powerNote}
				<p class="note">{card.powerNote}</p>
			{/if}
		</div>
	</dl>

	{#if card.strings.length > 0}
		<div class="strings">
			<h3>Strings im Vergleich (kWh je kWp)</h3>
			<ul>
				{#each card.strings as row (row.id)}
					<li class="string">
						<div class="line">
							<span class="name">
								<span class="dot" style:background={row.color} aria-hidden="true"></span>
								<span>{row.name}</span>
								{#if row.kwp !== null}
									<span class="meta">{formatKwp(row.kwp)}{NBSP}kWp</span>
								{/if}
							</span>
							<span class="values">
								{#if row.kwp !== null}<strong>{formatPerKwp(row.perKwp)}</strong
									>{SEPARATOR}{/if}{formatKwh(row.kwh)}
							</span>
							{#if admin}{@render pencil(row.name, row.id)}{/if}
						</div>
						{#if row.barPct !== null}
							<div class="bar" aria-hidden="true">
								<div class="fill" style:width="{row.barPct}%" style:background={row.color}></div>
							</div>
						{/if}
						{#if row.kwh === null && row.energyNote}
							<p class="note">{row.energyNote}</p>
						{:else if row.since}
							<p class="note">{row.since}</p>
						{/if}
					</li>
				{/each}
			</ul>
			<p class="note">
				Strings zusammen {card.stringsKwh === null ? DASH : formatKwh(card.stringsKwh)} (DC, vor dem Wechselrichter)
			</p>
		</div>
	{/if}

	{#if admin}
		<button type="button" class="add" onclick={() => onAddString(card.id)}>String hinzufügen</button
		>
	{/if}
</Card>

<style>
	.figures {
		display: grid;
		grid-template-columns: repeat(3, minmax(0, 1fr));
		gap: 0.5rem;
		margin: 0;
	}

	dt {
		color: var(--muted);
		font-size: 0.8125rem;
	}

	dd {
		margin: 0;
		font-size: 1.375rem;
		font-weight: 700;
		line-height: 1.3;
		overflow-wrap: anywhere;
	}

	.unit {
		font-size: 0.8125rem;
		font-weight: 500;
	}

	.note {
		margin: 0.125rem 0 0;
		color: var(--muted);
		font-size: 0.8125rem;
		overflow-wrap: anywhere;
	}

	.edit {
		display: grid;
		place-items: center;
		margin: -0.5rem -0.5rem 0 0;
		padding: 0;
		border: 0;
		background: transparent;
		color: var(--muted);
	}

	.edit svg {
		width: 1.125rem;
		height: 1.125rem;
		fill: none;
		stroke: currentColor;
		stroke-width: 2;
		stroke-linecap: round;
		stroke-linejoin: round;
	}

	.strings {
		display: grid;
		gap: 0.75rem;
		margin-top: 0.875rem;
		padding-top: 0.75rem;
		border-top: 1px solid var(--border);
	}

	h3 {
		margin: 0;
		color: var(--muted);
		font-size: 0.8125rem;
		font-weight: 400;
	}

	ul {
		display: grid;
		gap: 0.75rem;
		margin: 0;
		padding: 0;
		list-style: none;
	}

	.line {
		display: flex;
		align-items: center;
		gap: 0.5rem;
	}

	.name {
		flex: 1 1 auto;
		min-width: 0;
		display: flex;
		flex-wrap: wrap;
		align-items: baseline;
		column-gap: 0.375rem;
		overflow-wrap: anywhere;
	}

	.dot {
		flex: none;
		align-self: center;
		width: 0.625rem;
		height: 0.625rem;
		border-radius: 50%;
	}

	.meta {
		color: var(--muted);
		font-size: 0.8125rem;
	}

	.values {
		flex: none;
		white-space: nowrap;
	}

	.line .edit {
		margin: -0.5rem -0.5rem -0.5rem 0;
	}

	.bar {
		height: 0.625rem;
		margin-top: 0.375rem;
		border-radius: 999px;
		background: var(--surface-2);
		overflow: hidden;
	}

	.fill {
		height: 100%;
		border-radius: 999px;
	}

	.add {
		width: 100%;
		margin-top: 0.875rem;
		border-style: dashed;
		background: transparent;
		color: var(--accent);
		font-weight: 600;
	}
</style>
