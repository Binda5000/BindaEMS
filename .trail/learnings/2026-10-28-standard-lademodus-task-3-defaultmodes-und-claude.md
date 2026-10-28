---
title: "Standard-Lademodus Task 3: DefaultModes und Integrationstest mit respx-core"
author: Claude
date: 2026-10-28
branch: claude/project-thread-7dml9y
cost: { minutes: 35, tokens: 48616 }
id: l-86ebaaf7
scope: repo
status: active
src: agent
---

## Decided
- `DefaultModes` (src/bindaems/app/charging/default_mode.py) prüft „Bedienung nach dem Abstecken geht vor“ (gemerkter Modus aus `intents.cached()` weicht vom gelesenen ab) nur beim Nachholen (`_Wish.retry`), nicht beim ersten Versuch, weil der Cache beim ersten Versuch älter sein kann als die Absicht beim Abstecken (etwa eine Änderung per curl); sonst fiele der Wechsel ohne Grund aus. Das Fenster zwischen Lesen und Senden deckt die Vorbedingung ab.
- Die Vorbedingung vergleicht den Modus in `intents.cached()` mit dem gelesenen, weil `IntentService` den Cache bei jeder angenommenen Änderung unter seiner Sperre setzt: Eine Bedienung dazwischen steht dort. Die Version taugt nicht, weil der Plan sie laufend erhöht.

## Watch out
- Integrationstest der app mit nachgebildetem core: `make_runtime` aus tests/integration/test_app_runtime.py (core auf Port 1) plus `respx_mock.get/put("http://127.0.0.1:1/v1/intent").mock(side_effect=...)`; der `TestClient` für die API läuft daneben ungestört, weil respx seinen Transport nicht abfängt. Den Tracker startet der Test selbst (`asyncio.create_task(runtime.charging.run())`) und weckt ihn über `runtime.live_feed.health_handlers`; die Uhr zwischen Anstecken und Abstecken vorstellen, sonst hat die Session kein Fahrzeug.
