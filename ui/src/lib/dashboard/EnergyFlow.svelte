<script lang="ts">
	import IconShapes from '$lib/components/IconShapes.svelte';
	import { formatPower } from '$lib/format';
	import type { IconName } from '$lib/nav';
	import type { FlowBranch } from './flow';

	interface Props {
		branches: FlowBranch[];
		stale: boolean;
	}

	let { branches, stale }: Props = $props();

	const HUB = { x: 200, y: 150 };
	const NODE_R = 22;

	interface Placement {
		x: number;
		y: number;
		/** Beschriftung rechts neben dem Kreis statt darunter */
		side?: boolean;
	}

	const FIXED: Record<string, Placement> = {
		pv: { x: 200, y: 40, side: true },
		grid: { x: 52, y: 150 },
		house: { x: 348, y: 150 },
		battery: { x: 64, y: 262 }
	};

	const ICON: Record<string, IconName> = {
		pv: 'pv',
		grid: 'grid',
		battery: 'battery',
		house: 'house'
	};
	const COLOR: Record<string, string> = {
		pv: 'var(--pv)',
		grid: 'var(--grid)',
		battery: 'var(--battery)',
		house: 'var(--house)'
	};

	function placements(list: FlowBranch[]): Placement[] {
		const wallboxes = list.filter((b) => b.id.startsWith('wallbox:'));
		const spread = Math.min(120, 240 / Math.max(1, wallboxes.length));
		return list.map((b) => {
			if (b.id in FIXED) return FIXED[b.id];
			const index = wallboxes.indexOf(b);
			return { x: 245 + (index - (wallboxes.length - 1) / 2) * spread, y: 262 };
		});
	}

	/** Drei Geschwindigkeiten der laufenden Striche nach Leistung */
	function speed(powerW: number | null): 'slow' | 'medium' | 'fast' {
		if (powerW === null || powerW < 1000) return 'slow';
		return powerW < 4000 ? 'medium' : 'fast';
	}

	function describe(b: FlowBranch): string {
		if (b.direction === 'unknown') return `${b.label} unbekannt`;
		const caption = b.caption !== b.label ? ` ${b.caption}` : '';
		return `${b.label}${caption} ${formatPower(b.powerW)}`;
	}

	const places = $derived(placements(branches));
	const summary = $derived(
		`Energiefluss: ${branches.map(describe).join(', ')}${stale ? ' (veraltet)' : ''}`
	);
</script>

<figure class="flow" data-stale={stale} aria-label={summary}>
	{#if stale}
		<span class="stale">veraltet</span>
	{/if}
	<svg viewBox="0 0 400 330" aria-hidden="true">
		{#each branches as b, i (b.id)}
			{@const p = places[i]}
			<g
				data-testid="flow-{b.id}"
				data-direction={b.direction}
				style:--color={COLOR[b.id] ?? 'var(--wallbox)'}
			>
				<line
					class="line {b.direction} {speed(b.powerW)}"
					x1={p.x}
					y1={p.y}
					x2={HUB.x}
					y2={HUB.y}
				/>
				<circle class="node" cx={p.x} cy={p.y} r={NODE_R} />
				<g
					class="glyph"
					transform="translate({p.x - 12} {p.y - 12})"
					fill="none"
					stroke="currentColor"
					stroke-width="1.75"
					stroke-linecap="round"
					stroke-linejoin="round"
				>
					<IconShapes name={ICON[b.id] ?? 'wallbox'} />
				</g>
				{#if p.side}
					<text class="caption" x={p.x + 32} y={p.y - 4}>{b.caption}</text>
					<text class="power" x={p.x + 32} y={p.y + 14}>{formatPower(b.powerW)}</text>
				{:else}
					<text class="caption" x={p.x} y={p.y + 40} text-anchor="middle">{b.caption}</text>
					<text class="power" x={p.x} y={p.y + 58} text-anchor="middle">
						{formatPower(b.powerW)}
					</text>
				{/if}
			</g>
		{/each}
		<circle class="hub" cx={HUB.x} cy={HUB.y} r="9" />
	</svg>
</figure>

<style>
	.flow {
		position: relative;
		margin: 0;
	}

	svg {
		display: block;
		width: 100%;
		max-width: 32rem;
		height: auto;
		margin: 0 auto;
		overflow: visible;
	}

	.line {
		stroke: var(--color);
		stroke-width: 3;
		stroke-linecap: round;
		stroke-dasharray: 4 9;
		animation: run 2.4s linear infinite;
	}

	.line.medium {
		animation-duration: 1.4s;
	}

	.line.fast {
		animation-duration: 0.7s;
	}

	/* Linien laufen vom Zweig zum Hausanschluss: „in“ vorwärts, „out“ rückwärts */
	.line.out {
		animation-direction: reverse;
	}

	.line.idle {
		stroke-dasharray: none;
		stroke-width: 2;
		opacity: 0.35;
		animation: none;
	}

	.line.unknown {
		stroke: var(--muted);
		stroke-dasharray: 2 6;
		stroke-width: 2;
		opacity: 0.6;
		animation: none;
	}

	@keyframes run {
		to {
			stroke-dashoffset: -26;
		}
	}

	.node {
		fill: var(--surface);
		stroke: var(--color);
		stroke-width: 2.5;
	}

	.glyph {
		color: var(--color);
	}

	.hub {
		fill: var(--text);
	}

	text {
		fill: var(--text);
		font-size: 15px;
	}

	.caption {
		fill: var(--muted);
		font-size: 14px;
	}

	.power {
		font-weight: 600;
		font-variant-numeric: tabular-nums;
	}

	.flow[data-stale='true'] svg {
		opacity: 0.5;
	}

	.flow[data-stale='true'] .line {
		animation-play-state: paused;
	}

	.stale {
		position: absolute;
		top: 0;
		right: 0;
		padding: 0.125rem 0.5rem;
		border-radius: 999px;
		background: var(--surface-2);
		color: var(--warn);
		font-size: 0.8125rem;
		font-weight: 600;
	}

	@media (prefers-reduced-motion: reduce) {
		.line {
			animation: none;
		}
	}
</style>
