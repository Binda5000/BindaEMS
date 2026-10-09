# BindaEMS

Lokales Energiemanagementsystem (EMS) für Hausakku (Victron ESS), PV und zwei Wallboxen.
Ziel: maximaler Eigenverbrauch und minimale Stromkosten bei dynamischem Tarif – ohne Energiehandel,
mit harten Sicherheitsgrenzen (USV-Reserve, Hausanschluss) und nachvollziehbaren Entscheidungen.

## Dokumentation

- Design-Spezifikation: [`docs/superpowers/specs/2026-10-08-bindaems-design.md`](docs/superpowers/specs/2026-10-08-bindaems-design.md)
- Implementierungspläne: [`docs/superpowers/plans/`](docs/superpowers/plans/)
- Betrieb und Bedienung: [`docs/betrieb.md`](docs/betrieb.md)
- Prüfprotokolle und Abnahme: [`docs/verification/`](docs/verification/)

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

### Demo-Backend

Für die Arbeit am UI ohne Anlage: die echte ems-app mit festen Messwerten, ohne Geräte und ohne
Internet (Preise, Prognose und InfluxDB kommen aus Aufnahmen und einem Ersatz). Start im
Repository-Wurzelverzeichnis:

```bash
uv run python -m tests.e2e.ui_server              # API auf http://127.0.0.1:8099
uv run python -m tests.e2e.ui_server --ui-dir ui/build   # liefert zusätzlich das gebaute UI aus
```

- Benutzer `admin` (Admin), `sicher` (Admin mit TOTP, Geheimnis `JBSWY3DPEHPK3PXP`) und `gast`
  (Lesen), Passwort jeweils `demo-passwort-1`.
- Die Uhr steht auf dem 09.10.2026, 10:00 Ortszeit; die Messwerte schwanken jede Sekunde leicht.
- Jeder Start beginnt mit einem frischen Stand (neues Temp-Verzeichnis, außer mit `--data-dir`).
- Die Vertragsdateien `ui/src/lib/api/contract/*.json` entstehen aus derselben Demo-Welt:
  `UPDATE_UI_CONTRACT=1 uv run pytest tests/integration/test_ui_contract.py`.
