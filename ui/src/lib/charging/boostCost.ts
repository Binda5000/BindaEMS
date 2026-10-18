// Mehrkosten beim Boost (Plan 3c, Präzisierung 14): der Satz an der Fahrzeugkarte
import type { BoostCost } from '$lib/api/schemas';
import { formatEur, formatNumber, NBSP } from '$lib/format';

/**
 * „Boost kann bis zu 52,99 € Leistungspreis im Monat kosten (11,0 kW mehr Spitze).“ – nur wenn
 * im Monat ein Leistungspreis mit Satz gilt (`applies`) und das Fahrzeug darin vorkommt.
 */
export function boostCostText(cost: BoostCost | null | undefined, vehicle: string): string | null {
	if (!cost?.applies) return null;
	const entry = cost.vehicles.find((item) => item.id === vehicle);
	if (entry === undefined) return null;
	const kw = formatNumber(entry.extra_kw, 1);
	return `Boost kann bis zu ${formatEur(entry.cost_eur)} Leistungspreis im Monat kosten (${kw}${NBSP}kW mehr Spitze).`;
}
