# Akku-kWh über der Reserve Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Die Akku-Kachel der Übersicht zeigt unter „≈ 24 von 96 kWh“ zusätzlich die Energie, die über der USV-Reserve noch nutzbar ist („≈ 4,8 kWh über Reserve“).

**Architecture:** Nur UI. Eine neue reine Funktion `reserveEnergyText(view)` in `ui/src/lib/dashboard/battery.ts` rechnet aus `pct`, `reservePct` und `usableKwh` der vorhandenen `BatteryView`; `BatteryTile.svelte` zeigt sie als zweite Zeile in `.side`. Keine Änderung an core, app, API oder Datenbank.

**Tech Stack:** Svelte 5, TypeScript, Vitest, Testing Library.

**Spec:** `docs/superpowers/specs/2026-10-08-bindaems-design.md`, Abschnitt Übersicht, „Akku-Kachel“ (Zeile ~2151).

## Global Constraints

- Zahlen deutsch mit `formatNumberMax(x, 1)` und `NBSP` vor „kWh“, wie `energyText`.
- „USV-Reserve“ ist `limits.battery.reserve_soc_pct`; `usable_kwh` ist die nutzbare Kapazität (100 %).
- Ausrollen: nur ems-app, mit dem nächsten Sammel-Pull, nie allein.

## Entscheidungen

1. **Rechnung:** `max(0, pct − reservePct) / 100 × usableKwh` (Beispiel Screenshot: (25 − 20) % × 96 kWh = 4,8 kWh). Die Gesamtzeile „≈ 24 von 96 kWh“ bleibt („gerne auch beides“).
2. **Text:** „≈ 4,8 kWh über Reserve“; an oder unter der Reserve „nichts über Reserve“. Ohne SOC, Reserve oder Kapazität keine Zeile.
3. **Ort:** zweite Zeile rechts neben dem SOC, unter der Gesamtzeile, gleiche Schrift (gedämpft); kein „von 77 kWh“, damit die Zeile auf dem Handy nicht umbricht.
4. Batteriesymbol, Fußzeile und Plan bleiben unverändert.

## Review Focus

- SOC unter der Reserve (z. B. 15 % bei 20 %): nie negative kWh, sondern „nichts über Reserve“.
- SOC genau auf der Reserve: „nichts über Reserve“, nicht „≈ 0 kWh“.
- Kleiner Rest (0,04 kWh): zeigt „≈ 0 kWh über Reserve“ (rundet), kein Absturz.
- Veralteter SOC: Zeile erscheint wie die Gesamtzeile (gedämpft), „veraltet“ bleibt darunter.
- Fehlende Limits (`usableKwh` oder `reservePct` null): keine Zeile, keine „NaN“.

---

### Task 1: Zeile „über Reserve“ in der Akku-Kachel

**Files:**
- Modify: `ui/src/lib/dashboard/battery.ts` (neben `energyText`)
- Modify: `ui/src/lib/dashboard/BatteryTile.svelte` (`.side`)
- Test: `ui/src/lib/dashboard/battery.test.ts`, `ui/src/lib/dashboard/BatteryTile.test.ts`
- Docs: Spec „Akku-Kachel“ (Nachtrag 30.10.2026), `docs/uebergabe.md` (Stand, Offen an der Anlage: kommt mit dem nächsten Sammel-Pull)

**Interfaces:**
- Produces: `export function reserveEnergyText(view: BatteryView): string | null`

- [ ] **Step 1: Failing tests** in `battery.test.ts` (`describe('reserveEnergyText')`), mit `BatteryView`-Objekten:
  - pct 25, reserve 20, usable 96 → `/^≈ 4,8\skWh über Reserve$/`
  - pct 20, reserve 20 → `'nichts über Reserve'`; pct 15, reserve 20 → `'nichts über Reserve'`
  - pct null, reservePct null oder usableKwh null → `null`

  In `BatteryTile.test.ts`: der erste Test (pct 55, reserve 20, usable 30) findet zusätzlich `/^≈ 10,5\skWh über Reserve$/`; ein Test mit `pct: null, quality: 'missing'` findet keinen Text `/über Reserve/`.
- [ ] **Step 2:** `cd ui && pnpm vitest run src/lib/dashboard` → die neuen Tests scheitern (Funktion fehlt).
- [ ] **Step 3:** `reserveEnergyText` implementieren (Entscheidungen 1–2); in `BatteryTile.svelte` `const reserveEnergy = $derived(reserveEnergyText(view))` und im `{:else}`-Zweig nach der Gesamtzeile `{#if reserveEnergy}<span>{reserveEnergy}</span>{/if}`.
- [ ] **Step 4:** Tests grün; dann `pnpm check`, `pnpm lint`, `pnpm test`, E2E (`pnpm exec playwright test`, Breite 360 px muss passen) und die Python-Kette (unverändert, nur zur Sicherheit).
- [ ] **Step 5:** Spec-Nachtrag und `docs/uebergabe.md`, Commit `feat(ui): Akku-Kachel zeigt kWh über der USV-Reserve`.
