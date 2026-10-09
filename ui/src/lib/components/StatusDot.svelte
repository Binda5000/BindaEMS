<script lang="ts">
	interface Props {
		state: 'ok' | 'warn' | 'error' | 'unknown';
		label: string;
	}

	let { state, label }: Props = $props();
</script>

<span class="dot {state}" role="img" aria-label={label} title={label}></span>

<style>
	.dot {
		display: inline-block;
		width: 0.625rem;
		height: 0.625rem;
		border-radius: 50%;
		flex: none;
		background: var(--muted);
	}

	.ok {
		background: var(--ok);
	}

	.warn {
		background: var(--warn);
	}

	.error {
		background: var(--error);
	}

	.unknown {
		background: transparent;
		border: 2px solid var(--muted);
	}
</style>
