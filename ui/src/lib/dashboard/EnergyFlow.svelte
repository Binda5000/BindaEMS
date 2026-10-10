<script lang="ts">
	import IconShapes from '$lib/components/IconShapes.svelte';
	import { formatPower } from '$lib/format';
	import type { IconName } from '$lib/nav';
	import { IDLE_W, type FlowBranch, type FlowConsumer } from './flow';

	interface Props {
		branches: FlowBranch[];
		/** Verbraucher unter dem Haus (leer: keine angelegt) */
		consumers?: FlowConsumer[];
		stale: boolean;
	}

	let { branches, consumers = [], stale }: Props = $props();

	const WIDTH = 400;
	const HUB = { x: 200, y: 150 };
	const NODE_R = 22;
	const ROW_Y = 262; // Haus und Wallboxen
	const BUS_Y = 348; // Sammelschiene unter dem Haus
	const CONSUMER_Y = 400;
	const CONSUMER_R = 16;

	type Label = 'below' | 'right' | 'left';

	interface Placement {
		x: number;
		y: number;
		label: Label;
	}

	// Quellen oben und seitlich, Verbraucher unten; das Haus direkt unter dem Hausanschluss
	const FIXED: Record<string, Placement> = {
		pv: { x: 200, y: 44, label: 'right' },
		grid: { x: 52, y: 150, label: 'below' },
		battery: { x: 348, y: 150, label: 'below' },
		house: { x: 200, y: ROW_Y, label: 'right' }
	};
	// Wallboxen rechts und links neben dem Haus, weitere näher heran
	const WALLBOX_X = [348, 52, 122, 278];

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
		return list.map((b) => {
			if (b.id in FIXED) {
				// mehr als zwei Wallboxen: die Beschriftung des Hauses links, damit nichts überlappt
				if (b.id === 'house' && wallboxes.length > 3) return { ...FIXED.house, label: 'left' };
				return FIXED[b.id];
			}
			const index = wallboxes.indexOf(b);
			const x = WALLBOX_X[index] ?? 200 + (index % 2 ? -1 : 1) * 40 * index;
			return { x, y: ROW_Y, label: 'below' };
		});
	}

	/** Drei Geschwindigkeiten der laufenden Striche nach Leistung */
	function speed(powerW: number | null): 'slow' | 'medium' | 'fast' {
		if (powerW === null || powerW < 1000) return 'slow';
		return powerW < 4000 ? 'medium' : 'fast';
	}

	function flowOf(powerW: number | null): 'out' | 'idle' | 'unknown' | 'implausible' {
		if (powerW === null || !Number.isFinite(powerW)) return 'unknown';
		if (powerW < -IDLE_W) return 'implausible';
		return powerW > IDLE_W ? 'out' : 'idle';
	}

	/** Name auf die Breite seiner Spalte gekürzt; der volle Name steht im Tooltip */
	function shorten(name: string, max: number): string {
		return name.length <= max ? name : `${name.slice(0, Math.max(1, max - 1))}…`;
	}

	function describe(b: FlowBranch): string {
		if (b.direction === 'unknown') return `${b.label} unbekannt`;
		const caption = b.caption !== b.label ? ` ${b.caption}` : '';
		return `${b.label}${caption} ${formatPower(b.powerW)}`;
	}

	const places = $derived(placements(branches));
	const house = $derived(branches.find((b) => b.id === 'house'));

	const columns = $derived.by(() => {
		const count = consumers.length;
		const spacing = Math.min(84, (WIDTH - 24) / Math.max(1, count));
		return consumers.map((c, index) => ({
			consumer: c,
			x: WIDTH / 2 + (index - (count - 1) / 2) * spacing,
			chars: Math.max(4, Math.floor(spacing / 8.6)), // passt auch zur größeren Schrift am Handy
			flow: flowOf(c.powerW)
		}));
	});
	const busFrom = $derived(Math.min(HUB.x, ...columns.map((c) => c.x)));
	const busTo = $derived(Math.max(HUB.x, ...columns.map((c) => c.x)));
	const height = $derived(consumers.length > 0 ? CONSUMER_Y + 58 : ROW_Y + 72);

	const summary = $derived.by(() => {
		const parts = branches.map(describe);
		if (consumers.length > 0) {
			const inner = consumers.map((c) => `${c.name} ${formatPower(c.powerW)}`).join(', ');
			parts.push(`im Haus: ${inner}`);
		}
		return `Energiefluss: ${parts.join(', ')}${stale ? ' (veraltet)' : ''}`;
	});
</script>

<figure class="flow" data-stale={stale} aria-label={summary}>
	{#if stale}
		<span class="stale">veraltet</span>
	{/if}
	<svg viewBox="0 0 {WIDTH} {height}" aria-hidden="true">
		{#if columns.length > 0 && house}
			{@const trunk = flowOf(house.powerW)}
			<g class="consumers" style:--color="var(--house)">
				<path class="line {trunk} {speed(house.powerW)}" d="M{HUB.x} {ROW_Y + NODE_R} V{BUS_Y}" />
				<path class="bus" d="M{busFrom} {BUS_Y} H{busTo}" />
			</g>
			{#each columns as column (column.consumer.id)}
				{@const c = column.consumer}
				<g
					class="consumer"
					data-testid="flow-{c.id}"
					data-direction={column.flow}
					style:--color={c.color ?? 'var(--muted)'}
				>
					<title>{c.name}: {formatPower(c.powerW)}</title>
					<path
						class="line {column.flow} {speed(c.powerW)}"
						d="M{column.x} {BUS_Y} V{CONSUMER_Y - CONSUMER_R}"
					/>
					<circle class="node" cx={column.x} cy={CONSUMER_Y} r={CONSUMER_R} />
					<g
						class="glyph"
						transform="translate({column.x - 9} {CONSUMER_Y - 9}) scale(0.75)"
						fill="none"
						stroke="currentColor"
						stroke-width="2"
						stroke-linecap="round"
						stroke-linejoin="round"
					>
						<IconShapes name="consumers" />
					</g>
					<text class="caption small" x={column.x} y={CONSUMER_Y + 32} text-anchor="middle">
						{shorten(c.name, column.chars)}
					</text>
					<text class="power small" x={column.x} y={CONSUMER_Y + 49} text-anchor="middle">
						{formatPower(c.powerW)}
					</text>
				</g>
			{/each}
		{/if}

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
				<circle class="halo" cx={p.x} cy={p.y} r={NODE_R + 5} />
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
				{#if p.label === 'below'}
					<text class="caption" x={p.x} y={p.y + 42} text-anchor="middle">{b.caption}</text>
					<text class="power" x={p.x} y={p.y + 60} text-anchor="middle">
						{formatPower(b.powerW)}
					</text>
				{:else}
					{@const dx = p.label === 'right' ? 32 : -32}
					{@const anchor = p.label === 'right' ? 'start' : 'end'}
					<text class="caption" x={p.x + dx} y={p.y - 4} text-anchor={anchor}>{b.caption}</text>
					<text class="power" x={p.x + dx} y={p.y + 15} text-anchor={anchor}>
						{formatPower(b.powerW)}
					</text>
				{/if}
			</g>
		{/each}
		<circle class="hub-ring" cx={HUB.x} cy={HUB.y} r="13" />
		<circle class="hub" cx={HUB.x} cy={HUB.y} r="7" />
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
		max-width: 34rem;
		height: auto;
		margin: 0 auto;
		overflow: visible;
	}

	.line {
		fill: none;
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

	/* Linien zum Hausanschluss laufen „in“ vorwärts, „out“ rückwärts; unter dem Haus laufen
	   die Pfade vom Haus weg, also vorwärts */
	line.out {
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

	.line.implausible {
		stroke: var(--warn);
		stroke-dasharray: 2 6;
		stroke-width: 2;
		animation: none;
	}

	.bus {
		fill: none;
		stroke: var(--color);
		stroke-width: 2;
		stroke-linecap: round;
		opacity: 0.35;
	}

	[data-direction='implausible'] .caption,
	[data-direction='implausible'] .power {
		fill: var(--warn);
	}

	@keyframes run {
		to {
			stroke-dashoffset: -26;
		}
	}

	.halo {
		fill: var(--color);
		opacity: 0.12;
	}

	[data-direction='idle'] .halo,
	[data-direction='unknown'] .halo {
		opacity: 0;
	}

	.node {
		fill: var(--surface);
		stroke: var(--color);
		stroke-width: 2.5;
	}

	.consumer .node {
		stroke-width: 2;
	}

	.glyph {
		color: var(--color);
	}

	.hub-ring {
		fill: var(--surface);
		stroke: var(--border);
		stroke-width: 2;
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

	.small {
		font-size: 13px;
	}

	/* schmale Karten verkleinern das Diagramm; die Schrift wächst dagegen, damit sie lesbar bleibt */
	@media (max-width: 30rem) {
		text {
			font-size: 18px;
		}

		.caption {
			font-size: 17px;
		}

		.small {
			font-size: 15px;
		}
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
