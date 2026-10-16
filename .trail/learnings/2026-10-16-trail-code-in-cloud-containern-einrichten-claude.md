---
title: Trail-code in Cloud-Containern einrichten
author: Claude
date: 2026-10-16
branch: claude/project-thread-q161q6
cost: { minutes: 1, tokens: 1023 }
touches:
  - ".claude/settings.json"
  - ".trail/hook.cjs"
---

## Decided
- `trail init --agents claude` committet nur `.claude/settings.json` (SessionStart-Hook) und `.trail/` (hook.cjs, README, .gitignore). Cache, Statusline und `.claude/settings.local.json` bleiben lokal.
- Committet auf dem Sitzungsbranch, nicht auf main; Merge nach main nur mit Zustimmung des Betreibers.

## Tried and ruled out
- Sich auf die globale Installation verlassen: in der Cloud geht `npm install -g`, der Code-Map-Cache und alles unter `~/.claude` mit dem Container verloren.

## Watch out
- Für dauerhaftes Trail in Cloud-Sitzungen gehört `npm install -g @trailhq/trail-code && trail build` ins Setup-Skript der Umgebung.
- Ohne installiertes CLI ist der Hook harmlos: er listet nur `.trail/learnings/`.
- `trail init` schreibt auch nach `~/.claude` (Skill, Hooks); mit `--no-global` lässt sich das vermeiden.
