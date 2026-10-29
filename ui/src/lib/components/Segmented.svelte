<script lang="ts">
	type Id = string | number;

	interface Props {
		options: { id: Id; label: string }[];
		/** Kennung der gewählten Option */
		value: Id;
		/** Name der Gruppe für Screenreader */
		label: string;
		onSelect: (id: Id) => void;
	}

	let { options, value, label, onSelect }: Props = $props();
</script>

<!-- eine Zeile gleich breiter Knöpfe, die gewählte gefüllt -->
<div
	class="segmented"
	role="group"
	aria-label={label}
	style:grid-template-columns="repeat({options.length}, minmax(0, 1fr))"
>
	{#each options as option (option.id)}
		<button
			type="button"
			class:active={option.id === value}
			aria-pressed={option.id === value}
			onclick={() => onSelect(option.id)}
		>
			{option.label}
		</button>
	{/each}
</div>

<style>
	.segmented {
		display: grid;
		border: 1px solid var(--border);
		border-radius: 8px;
		overflow: hidden;
	}

	button {
		min-height: 2.75rem;
		min-width: 0;
		padding: 0.375rem 0.25rem;
		border: 0;
		border-radius: 0;
		background: var(--surface-2);
		text-align: center;
	}

	button + button {
		border-left: 1px solid var(--border);
	}

	/* der Rahmen der Gruppe schneidet den Fokusring sonst ab */
	button:focus-visible {
		outline-offset: -3px;
	}

	button.active {
		background: var(--accent);
		color: var(--accent-text);
		font-weight: 600;
	}
</style>
