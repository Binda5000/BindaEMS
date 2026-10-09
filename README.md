# BindaEMS

Lokales Energiemanagementsystem (EMS) für Hausakku (Victron ESS), PV und zwei Wallboxen.
Ziel: maximaler Eigenverbrauch und minimale Stromkosten bei dynamischem Tarif – ohne Energiehandel,
mit harten Sicherheitsgrenzen (USV-Reserve, Hausanschluss) und nachvollziehbaren Entscheidungen.

## Dokumentation

- Design-Spezifikation: [`docs/superpowers/specs/2026-10-08-bindaems-design.md`](docs/superpowers/specs/2026-10-08-bindaems-design.md)
- Implementierungspläne: [`docs/superpowers/plans/`](docs/superpowers/plans/)

## Entwicklung

Voraussetzung: [uv](https://docs.astral.sh/uv/) und Python 3.12.

```bash
uv sync --extra core --extra app --extra dev   # Abhängigkeiten installieren
uv run pytest                       # Tests
uv run ruff check . && uv run mypy src && uv run lint-imports   # Lint, Typen, Importgrenzen
```

### Web-UI entwickeln

Voraussetzung: Node 22 und pnpm (über `corepack enable` die Version aus `ui/package.json`).

```bash
cd ui
pnpm install    # Abhängigkeiten installieren
pnpm dev        # Entwicklungsserver; /api geht an das Demo-Backend (127.0.0.1:8099)
BINDAEMS_BACKEND=http://<host>:8080 pnpm dev   # stattdessen eine echte ems-app
pnpm test       # Vitest
pnpm check      # Typprüfung (svelte-check)
pnpm lint       # Prettier und ESLint
pnpm build      # statisches SPA nach ui/build
```
