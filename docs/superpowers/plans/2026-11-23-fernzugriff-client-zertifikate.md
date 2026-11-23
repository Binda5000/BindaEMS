# Fernzugriff über HAProxy mit Client-Zertifikaten Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Die Web-UI von BindaEMS ist von unterwegs über den vorhandenen HAProxy erreichbar, aber nur mit einem Client-Zertifikat aus Bindas eigener CA (mTLS); die app prüft das als zweite Linie selbst nach.

**Architecture:** HAProxy terminiert TLS, verlangt für den EMS-Hostnamen ein gültiges, nicht widerrufenes Client-Zertifikat (CA-Datei und CRL) und meldet das Ergebnis der app in zwei Kopfzeilen. Die app übernimmt diese Kopfzeilen nur von `app.trusted_proxies` und weist mit `app.client_cert.required` jede Anfrage ab, die weder ein geprüftes Zertifikat noch eine Adresse aus dem Heimnetz hat; das fängt Fehlkonfigurationen des Proxys ab (z. B. die SNI/Host-Umgehung). Anmeldung, Rollen, Sitzungen und CSRF bleiben wie sie sind; Admins brauchen von unterwegs TOTP. Nur app; der core bleibt unverändert, das UI auch.

**Tech Stack:** Python 3.12, FastAPI/Starlette, uvicorn (`ProxyHeadersMiddleware`), pydantic, pytest; OpenSSL (Skript für die CA); HAProxy ≥ 2.4 an der Anlage.

**Spec:** `docs/superpowers/specs/2026-10-08-bindaems-design.md`, 1.4 „Nicht-Ziele“ („Öffentlicher Internetzugang: Zugriff von unterwegs läuft über VPN.“), Entscheidung E13 („Fernzugriff über VPN, TOTP optional“), 14 „Login und Anwendungssicherheit“ (Rollen, Sitzungen, „Vertrauen in `X-Forwarded-*` nur vom konfigurierten Reverse Proxy“, „Zugriff: HTTPS terminiert am Reverse Proxy; von außen nur über VPN.“), 15 „Deployment“. Betrieb: `docs/betrieb.md`, Abschnitt 6 „Reverse Proxy“. Neuer Absatz „Stand Plan Fernzugriff“ kommt mit Task 5.

## Befund

- **Spec heute:** Öffentlicher Zugriff ist Nicht-Ziel (1.4), E13 nennt als verworfene Alternative „öffentlicher Zugriff mit Pflicht-2FA“. Dieser Plan ändert das: öffentlich erreichbar über HAProxy, aber erst nach mTLS. Das Client-Zertifikat ist der Besitzfaktor (Gerät), das Passwort der Wissensfaktor. VPN bleibt als Weg erlaubt.
- **Die app ist auf einen Proxy vorbereitet:** `app.trusted_proxies` (uvicorn `proxy_headers`/`forwarded_allow_ips` in `runtime._serve_http`), `app.cookie_secure` (Standard `true`, Cookies `HttpOnly`, `Secure`, `SameSite=Strict`), CSRF per Double-Submit, CSP und weitere Security-Header (`api/server.py`), Sperre nach 5 Fehlversuchen in 15 min je Benutzer, optional TOTP. Der Live-WebSocket `/api/live` prüft `Origin` gegen `Host` und die Sitzung.
- **Was fehlt:** Die app weiß nicht, ob eine Anfrage mit Zertifikat kam. Prüft nur HAProxy, hängt die ganze Sicherheit an einer Zeile Proxy-Konfiguration. Bekannte Falle: Bei SNI-basierter Prüfung (`crt-list` mit `verify required` nur für `ems.…`) kann ein Angreifer die TLS-Verbindung mit dem SNI eines anderen Dienstes ohne Zertifikatspflicht aufbauen und dann `Host: ems.…` senden; HAProxy routet nach `Host` und die app ist ohne Zertifikat erreichbar. Dasselbe passiert ehrlichen Browsern durch HTTP/2-Connection-Coalescing (dann ohne Zertifikat statt mit).
- **Sperre als Angriffsfläche:** Öffentlich könnte jeder durch 5 falsche Passwörter das Admin-Konto 15 min sperren. Mit mTLS davor erreicht nur ein Gerät mit Zertifikat das Login; das Problem entfällt.
- **WebSocket mit Client-Zertifikaten:** Safari auf iOS hatte in der Vergangenheit Probleme, bei WebSockets und bei Web-Apps vom Home-Bildschirm ein Client-Zertifikat mitzuschicken. Ohne `/api/live` zeigt das UI „Verbindung …“ und keine Live-Werte. Das prüfen wir an der Anlage (Schritt A7); ein Ersatz über Abfragen ist nicht Teil dieses Plans (Entscheidung 4).
- **Docker-Healthcheck** fragt `http://127.0.0.1:8080/health` ohne Proxy ab; `/health` muss ohne Zertifikat gehen (liefert nur Status und Version).

## Optionen

**A – HAProxy prüft, die app prüft nach (empfohlen, dieser Plan).** mTLS am HAProxy, dazu eine kleine Zugangsprüfung in der app (`app.client_cert.required`), ab Werk aus. Schützt gegen die SNI/Host-Umgehung und gegen einen vergessenen Schalter am Proxy, protokolliert das Gerät (CN) bei der Anmeldung und erlaubt die TOTP-Pflicht für Admins von unterwegs.

**B – Nur HAProxy, die app bleibt unverändert.** Kein Code, nur Doku und Spec. Jeder Fehler in der Proxy-Konfiguration öffnet die app direkt dem Internet, und die app kann extern und Heimnetz nicht unterscheiden (kein TOTP-Zwang, kein Gerät im Log).

**C – VPN statt öffentlichem Zugang (Spec heute).** Am sichersten, aber nicht, was du willst.

## Entscheidungen (Vorschlag, wartet auf Freigabe)

1. **Heimnetz ohne Zertifikat:** Anfragen, deren echte Client-Adresse (nach `X-Forwarded-For` vom vertrauten Proxy) in `app.client_cert.exempt_networks` liegt, brauchen kein Zertifikat, so wie heute. Alles andere braucht eins. Voraussetzung: HAProxy sieht die echten öffentlichen Adressen (der Router maskiert Zugriffe von außen nicht auf eine LAN-Adresse); das prüfst du in A6. Alternative: alle Geräte mit Zertifikat, dann bleibt `exempt_networks` leer.
2. **Admin von unterwegs nur mit TOTP:** Eine Anmeldung von außen (`remote`) eines Admins ohne eingerichtetes TOTP lehnt die app nach richtigem Passwort ab (403, „Admins melden sich von unterwegs nur mit TOTP an. Richte TOTP im Heimnetz unter „Konto“ ein.“). Lesen und Bedienen brauchen nur Passwort und Zertifikat. Eine im Heimnetz begonnene Admin-Sitzung endet wie bisher nach 30 min Leerlauf; sie wird unterwegs nicht zusätzlich gesperrt.
3. **Eigene CA per Skript im Repo:** `deploy/fernzugriff/client-ca.sh` mit `init`, `add <gerät>`, `revoke <gerät>`, `crl`, `list`, getestet. Alternative: du nimmst XCA oder deine vorhandene PKI, dann entfällt Task 4 und Abschnitt 6 beschreibt nur die Anforderungen an die Zertifikate.
4. **Kein Polling-Ersatz für den WebSocket jetzt:** Erst wenn A7 zeigt, dass ein Gerät den Live-WebSocket mit Zertifikat nicht aufbaut, gibt es dafür einen eigenen kleinen Plan.
5. **Spec:** 1.4 und E13 lassen öffentlichen Zugriff zu, aber nur hinter mTLS am Reverse Proxy plus Nachprüfung in der app; VPN bleibt erlaubt. 14 „Zugriff“ entsprechend.
6. **Ausrollen:** nur app, keine Migration, kein Schritt an der UI, der core bleibt unverändert. `client_cert.required` ist ab Werk aus, das Image ändert also ohne Eintrag in `config.yaml` nichts. Kann mit jedem gemeinsamen Pull nach Stufe 2 mit (core und app weiter immer gemeinsam mit fester Version). Erst einschalten, wenn A1 bis A5 an der Anlage stehen.

## Kopfzeilen zwischen HAProxy und app (Vertrag)

| Kopfzeile | Wert | Wer setzt sie |
|---|---|---|
| `X-BindaEMS-Client-Cert` | `1`, nur wenn `ssl_c_used` und `ssl_c_verify` = 0 | HAProxy; vorher immer gelöscht |
| `X-BindaEMS-Client-CN` | CN des Zertifikats (Gerätename), höchstens 64 Zeichen | HAProxy; vorher immer gelöscht |
| `X-Forwarded-For`, `X-Forwarded-Proto` | wie heute (`option forwardfor`, `https`) | HAProxy |

Die app wertet beide `X-BindaEMS-*` nur aus, wenn die direkte Gegenstelle in `app.trusted_proxies` liegt; von jeder anderen Adresse ignoriert sie sie.

## Global Constraints

- Deutsch, du; Commits im Stil `feat(app): …`, `feat(deploy): …`, `docs: …`; keine Modellbezeichnungen im Repo.
- Prüfkette vor jedem Commit: `ruff format --check`, `ruff check`, `mypy --strict` (src + cerbo-watchdog), `lint-imports`, `pytest` mit Abdeckung (≥ 75 % gesamt, ≥ 90 % safety/control/actuators); UI: `pnpm check`, `pnpm lint`, `pnpm test`. In der Cloud vor uv: `export UV_SYSTEM_CERTS=1; unset UV_NATIVE_TLS`.
- Der core bleibt unverändert. Kein Aktor wird geschaltet. Secrets nur über `BINDAEMS_*`-Umgebungsvariablen; das Repo enthält keine Schlüssel, keine Zertifikate, keine Hostnamen der Anlage.
- Ab Werk (`client_cert.required: false`) verhält sich die app wie heute: dieselben Antworten, dieselbe Auswertung von `X-Forwarded-*` (bestehende Tests bleiben grün).
- Spec 14 gilt weiter: „Vertrauen in `X-Forwarded-*` nur vom konfigurierten Reverse Proxy“, Cookies `HttpOnly`, `Secure`, `SameSite=Strict`.

## Review Focus

- **Gefälschte Kopfzeilen direkt an Port 8080** (Gegenstelle nicht in `trusted_proxies`, sendet `X-BindaEMS-Client-Cert: 1` und `X-Forwarded-For: 192.168.1.5`): abgewiesen, weder Zertifikat noch Heimnetz gelten. Test in Task 2 (`test_spoofed_headers_from_untrusted_peer_are_ignored`).
- **SNI/Host-Umgehung am Proxy** (Anfrage vom Proxy mit öffentlicher Client-Adresse, ohne `X-BindaEMS-Client-Cert`): 403 und WebSocket geschlossen, bevor er angenommen wird. Tests in Task 2 (`test_proxy_request_without_cert_is_rejected`, `test_websocket_without_cert_is_rejected`).
- **Mehrere Einträge in `X-Forwarded-For`** (Client schickt selbst `X-Forwarded-For: 192.168.1.5`, HAProxy hängt die echte Adresse `203.0.113.7` an): zählt die echte, also kein Heimnetz. Test in Task 2 (`test_client_supplied_forwarded_for_does_not_count_as_lan`).
- **Docker-Healthcheck und Proxy-Healthcheck** (`/health` von 127.0.0.1 bzw. vom Proxy ohne Zertifikat): 200. Test in Task 2 (`test_health_needs_no_cert`).
- **Admin mit falschem Passwort von unterwegs:** wie bisher 401 und Fehlversuch gezählt; die TOTP-Pflicht verrät die Rolle erst nach richtigem Passwort. Test in Task 3 (`test_remote_admin_wrong_password_counts_as_failure`).

---

### Task 1: Einstellung `app.client_cert`

**Files:**
- Modify: `src/bindaems/shared/config.py` (`AppConfig`, ~Zeile 583)
- Modify: `deploy/config.example.yaml` (Block `app:`, ~Zeile 150)
- Test: `tests/unit/shared/test_config.py`

**Interfaces:**
- Produces: `class ClientCertConfig(_Model)` mit `required: bool = False` und `exempt_networks: list[str] = []` (jede Angabe muss `ipaddress.ip_network(entry, strict=False)` bestehen); `AppConfig.client_cert: ClientCertConfig = Field(default_factory=ClientCertConfig)`.

- [ ] **Step 1: Write the failing tests** (im Stil der vorhandenen `write_cfg`-Tests)

```python
def test_client_cert_defaults_off(tmp_path: Path) -> None:
    cfg = load_config(write_cfg(tmp_path, lambda d: None))
    assert cfg.app.client_cert.required is False
    assert cfg.app.client_cert.exempt_networks == []

def test_client_cert_required_needs_trusted_proxy(tmp_path: Path) -> None:
    p = write_cfg(tmp_path, lambda d: d["app"].update(
        trusted_proxies=[], client_cert={"required": True}))
    with pytest.raises(ConfigError, match="client_cert.required verlangt app.trusted_proxies"):
        load_config(p)

def test_client_cert_required_needs_secure_cookies(tmp_path: Path) -> None:
    p = write_cfg(tmp_path, lambda d: d["app"].update(
        trusted_proxies=["192.168.1.10"], cookie_secure=False, client_cert={"required": True}))
    with pytest.raises(ConfigError, match="client_cert.required verlangt app.cookie_secure"):
        load_config(p)

@pytest.mark.parametrize("value", ["lan", "192.168.1.0/33"])
def test_exempt_networks_must_be_networks(tmp_path: Path, value: str) -> None:
    p = write_cfg(tmp_path, lambda d: d["app"].update(client_cert={"exempt_networks": [value]}))
    with pytest.raises(ConfigError, match="exempt_networks"):
        load_config(p)
```

- [ ] **Step 2: Run** `uv run pytest tests/unit/shared/test_config.py -k client_cert -v` → FAIL (unbekanntes Feld / fehlende Prüfung).
- [ ] **Step 3: Implement** `ClientCertConfig` und einen `model_validator(mode="after")` in `AppConfig` mit genau den Texten `"client_cert.required verlangt app.trusted_proxies"` und `"client_cert.required verlangt app.cookie_secure: true"`. Die Netzprüfung teilt sich die Logik mit `_proxies_are_networks` (eine Hilfsfunktion). In `deploy/config.example.yaml` unter `app:` auskommentiert:

```yaml
  # Fernzugriff mit Client-Zertifikaten (docs/betrieb.md, Abschnitt 6): erst einschalten, wenn HAProxy
  # mTLS prüft und die Kopfzeilen X-BindaEMS-Client-Cert/-CN setzt
  # client_cert:
  #   required: true
  #   exempt_networks: [192.168.1.0/24]   # Heimnetz ohne Zertifikat; leer = jedes Gerät braucht eins
```

- [ ] **Step 4: Run** dieselben Tests und `tests/unit/test_deploy_files.py` → PASS.
- [ ] **Step 5: Commit** `feat(app): Einstellung client_cert für den Fernzugriff`

### Task 2: Zugangsprüfung in der app

**Files:**
- Create: `src/bindaems/app/api/access.py`
- Modify: `src/bindaems/app/api/server.py` (`create_app`)
- Modify: `src/bindaems/app/runtime.py` (`create_app`-Aufruf ~Zeile 593, `_serve_http` ~Zeile 941)
- Test: `tests/unit/app/api/test_access.py`

**Interfaces:**
- Consumes: `ClientCertConfig` (Task 1).
- Produces (in `bindaems.app.api.access`):
  - `CERT_HEADER = "x-bindaems-client-cert"`, `CN_HEADER = "x-bindaems-client-cn"`, `CN_MAX = 64`
  - `@dataclass(frozen=True) class Access: client_ip: str | None; via_proxy: bool; remote: bool; device: str | None` (`remote` = nicht aus `exempt_networks`; `device` = CN nur bei geprüftem Zertifikat über den Proxy, auf `CN_MAX` gekürzt, sonst `None`)
  - `def access_of(request: Request) -> Access | None` (liest `request.scope["bindaems.access"]`)
  - `class PeerMarker` (ASGI, außen): legt die rohe Gegenstelle `scope["client"][0]` unter `scope["bindaems.peer"]` ab, bevor sie umgeschrieben wird.
  - `class AccessGate(app, *, trusted_proxies: Sequence[str], client_cert: ClientCertConfig)` (ASGI, innen hinter uvicorns `ProxyHeadersMiddleware`): berechnet `Access` aus `scope["bindaems.peer"]`, dem umgeschriebenen `scope["client"]` und den Kopfzeilen, legt es unter `scope["bindaems.access"]` ab und entscheidet.
  - `create_app(routers, *, ui_dir=None, trusted_proxies: Sequence[str] = (), client_cert: ClientCertConfig | None = None) -> FastAPI` fügt in dieser Reihenfolge hinzu: `AccessGate`, `ProxyHeadersMiddleware(trusted_hosts=list(trusted_proxies))`, `PeerMarker` (zuletzt hinzugefügt = außen).

Regel für `required = True` (für HTTP und WebSocket gleich):

| Fall | Ergebnis |
|---|---|
| Pfad `/health` | erlaubt |
| echte Client-Adresse in `exempt_networks` | erlaubt, `remote = False` |
| Gegenstelle in `trusted_proxies` und `X-BindaEMS-Client-Cert` = `1` | erlaubt, `remote = True`, `device` = CN |
| sonst | HTTP: 403 `{"detail": "Zugriff nur mit Client-Zertifikat"}`; WebSocket: `websocket.close` (Code 1008) vor `accept` |

Mit `required = False` blockiert die Prüfung nie, legt aber `Access` ab (für Task 3 und das Log).

- [ ] **Step 1: Write the failing tests** mit `starlette.testclient.TestClient(app, client=(ip, port))` gegen `create_app([router mit /api/ping und WebSocket /api/live-test], trusted_proxies=["10.0.0.2"], client_cert=ClientCertConfig(required=True, exempt_networks=["192.168.1.0/24"]))`:

```python
def test_lan_client_needs_no_cert():  # direkt aus 192.168.1.5 → 200
def test_proxy_request_with_verified_cert_passes():
    # client=("10.0.0.2", …), X-Forwarded-For: 203.0.113.7, X-BindaEMS-Client-Cert: 1, X-BindaEMS-Client-CN: handy-binda
    # → 200; Access(client_ip="203.0.113.7", via_proxy=True, remote=True, device="handy-binda")
def test_proxy_request_without_cert_is_rejected():  # wie oben ohne Cert-Kopf → 403, detail "Zugriff nur mit Client-Zertifikat"
def test_proxy_lan_client_passes_without_cert():  # vom Proxy, X-Forwarded-For: 192.168.1.20 → 200, remote False
def test_client_supplied_forwarded_for_does_not_count_as_lan():
    # vom Proxy, X-Forwarded-For: "192.168.1.5, 203.0.113.7", ohne Cert → 403
def test_spoofed_headers_from_untrusted_peer_are_ignored():
    # client=("203.0.113.9", …) mit Cert-Kopf 1 und X-Forwarded-For 192.168.1.5 → 403
def test_websocket_without_cert_is_rejected():  # pytest.raises(WebSocketDisconnect) mit code 1008
def test_websocket_with_cert_passes():
def test_health_needs_no_cert():  # client=("127.0.0.1", …) und vom Proxy ohne Kopf → 200
def test_cn_is_truncated_and_ignored_without_cert():  # 80 Zeichen → 64; CN ohne Cert-Kopf → device None
def test_off_never_blocks_but_records_access():  # required=False: 203.0.113.9 → 200, Access.remote True
def test_forwarded_for_still_used_for_client_ip_when_off():  # wie heute: client_ip aus X-Forwarded-For nur vom Proxy
```

- [ ] **Step 2: Run** `uv run pytest tests/unit/app/api/test_access.py -v` → FAIL (Modul fehlt).
- [ ] **Step 3: Implement** `access.py` und die Erweiterung von `create_app`. Die echte Client-Adresse ist `scope["client"][0]` nach `ProxyHeadersMiddleware` (sie nimmt von rechts die erste nicht vertraute Adresse aus `X-Forwarded-For`, nur wenn die Gegenstelle vertraut ist). `via_proxy` prüft die rohe Gegenstelle aus `PeerMarker` gegen dieselben Netze. Eine Abweisung loggt `log.warning("Zugriff ohne Client-Zertifikat abgewiesen", ip=…, path=…)`, höchstens einmal je Adresse und Minute.
- [ ] **Step 4: Runtime umstellen:** `runtime.py` übergibt `trusted_proxies=cfg.app.trusted_proxies, client_cert=cfg.app.client_cert` an `create_app`; `_serve_http` setzt `proxy_headers=False` und lässt `forwarded_allow_ips` weg (die Auswertung macht jetzt `create_app`, einmal). Bestehende Tests, die `_serve_http` oder die Client-Adresse im Login-Log prüfen, bleiben grün.
- [ ] **Step 5: Run** `uv run pytest tests/unit/app -q` → PASS; dann die volle Prüfkette.
- [ ] **Step 6: Commit** `feat(app): Zugang von außen nur mit geprüftem Client-Zertifikat`

### Task 3: Anmeldung von unterwegs

**Files:**
- Modify: `src/bindaems/app/auth/service.py` (`login`, `_login`, neue Ausnahme)
- Modify: `src/bindaems/app/auth/routes.py` (`login`, `_client_ip`)
- Test: `tests/unit/app/auth/test_service.py`, `tests/unit/app/auth/test_routes.py` (oder die vorhandene Datei für Routen)

**Interfaces:**
- Consumes: `access_of(request) -> Access | None` (Task 2).
- Produces: `class AdminTotpRequiredError(AuthError)` mit dem Text `"Admins melden sich von unterwegs nur mit TOTP an. Richte TOTP im Heimnetz unter „Konto“ ein."`; `AuthService.login(username, password, *, totp: str | None, remember: bool, remote: bool = False) -> NewSession`.

- [ ] **Step 1: Write the failing tests**

```python
def test_remote_admin_without_totp_is_refused(auth):  # richtiges Passwort, remote=True → AdminTotpRequiredError, keine Sitzung angelegt
def test_remote_admin_with_totp_logs_in(auth):
def test_remote_operator_without_totp_logs_in(auth):  # Rolle "operator"/Bedienen
def test_lan_admin_without_totp_logs_in(auth):  # remote=False wie bisher
def test_remote_admin_wrong_password_counts_as_failure(auth):  # InvalidCredentialsError, Fehlversuch gezählt
def test_login_route_returns_403_for_remote_admin_without_totp(client):  # über Proxy mit Cert-Kopf → 403 + Text
def test_login_log_names_device(client, caplog/structlog capture):  # "Anmeldung erfolgreich" mit device="handy-binda"
```

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement:** In `_login` nach erfolgreicher Passwort- und TOTP-Prüfung: `if remote and row.role == "admin" and not row.totp_enabled: raise AdminTotpRequiredError`, bevor eine Sitzung entsteht (Fehlversuche wie bei einer erfolgreichen Prüfung zurücksetzen). Die Route nimmt `remote` aus `access_of(request)` (`False` ohne Access), antwortet auf `AdminTotpRequiredError` mit 403 und `{"detail": …}` und schreibt bei jeder Anmeldung `device=access.device` ins Log. `_client_ip` nimmt `access.client_ip`. Das UI zeigt `detail` schon heute an (`LoginForm.svelte`), es ändert sich nicht.
- [ ] **Step 4: Run** `uv run pytest tests/unit/app/auth -q` → PASS, dann die Prüfkette.
- [ ] **Step 5: Commit** `feat(app): Admins von unterwegs nur mit TOTP`

### Task 4: Skript für die eigene CA

**Files:**
- Create: `deploy/fernzugriff/client-ca.sh` (ausführbar, `set -euo pipefail`, nur `openssl` ≥ 3)
- Test: `tests/unit/test_client_ca.py` (ruft das Skript per `subprocess` in `tmp_path` auf; `pytest.skip`, wenn `openssl` fehlt)

**Interfaces:**
- Produces: `client-ca.sh <befehl>` mit `CA_DIR` (Standard `./bindaems-client-ca`), Passwörter aus `BINDAEMS_CA_PASS` (Schlüssel der CA) und `BINDAEMS_P12_PASS` (Export je Gerät), sonst Abfrage am Terminal.
  - `init` – CA-Schlüssel EC P-256, verschlüsselt (AES-256), CA-Zertifikat 10 Jahre, CN „BindaEMS Client-CA“, `basicConstraints=critical,CA:TRUE,pathlen:0`, `keyUsage=critical,keyCertSign,cRLSign`; legt `index.txt`, `serial`, `crlnumber` an und schreibt `ca.pem` und `crl.pem`. Bricht ab, wenn `CA_DIR` schon eine CA hat.
  - `add <gerät>` – `<gerät>` nur `[a-z0-9-]{1,64}`; Schlüssel EC P-256, Zertifikat 825 Tage, `CN=<gerät>`, `extendedKeyUsage=clientAuth`, `keyUsage=critical,digitalSignature`; schreibt `geraete/<gerät>.p12` im alten Format, das iOS und Android lesen (`-certpbe PBE-SHA1-3DES -keypbe PBE-SHA1-3DES -macalg sha1`), mit CA im Bündel. Bricht ab, wenn das Gerät schon ein gültiges Zertifikat hat.
  - `revoke <gerät>` – widerruft und schreibt danach `crl.pem` neu.
  - `crl` – schreibt `crl.pem` neu, gültig 365 Tage.
  - `list` – Gerät, Seriennummer, gültig bis, Status (gültig/widerrufen) und „CRL gültig bis …“ mit Warnung ab 30 Tagen vor Ablauf.

- [ ] **Step 1: Write the failing tests**

```python
def test_init_creates_ca_and_crl(ca):  # ca.pem, crl.pem; openssl x509 zeigt CA:TRUE
def test_add_creates_client_cert_signed_by_ca(ca):  # openssl verify -CAfile ca.pem → OK; EKU "TLS Web Client Authentication"; CN=handy-binda
def test_p12_opens_with_password(ca):  # openssl pkcs12 -in … -passin env:BINDAEMS_P12_PASS -nokeys → enthält CN
def test_revoke_puts_cert_on_crl(ca):  # openssl verify -crl_check -CRLfile crl.pem → "certificate revoked"
def test_device_name_is_validated(ca):  # "Handy Binda" → Exit ≠ 0, Meldung „Gerätename …“
def test_init_refuses_existing_ca(ca):
def test_list_shows_crl_expiry(ca):  # Ausgabe enthält "CRL gültig bis"
```

- [ ] **Step 2: Run** `uv run pytest tests/unit/test_client_ca.py -v` → FAIL.
- [ ] **Step 3: Implement** das Skript mit einer eigenen `openssl.cnf` je `CA_DIR` (Abschnitt `[ca]` für `openssl ca`, `default_md = sha256`, `copy_extensions = none`, `unique_subject = no`). Kein Schlüssel verlässt `CA_DIR`; Rechte `0700` für `CA_DIR`, `0600` für Schlüssel und `.p12`.
- [ ] **Step 4: Run** → PASS, dann die Prüfkette.
- [ ] **Step 5: Commit** `feat(deploy): Skript für die Client-CA des Fernzugriffs`

### Task 5: Betriebshandbuch, Spec und Übergabe

**Files:**
- Modify: `docs/betrieb.md`, Abschnitt 6 „Reverse Proxy“ (neuer Unterabschnitt „Fernzugriff mit Client-Zertifikaten (HAProxy)“) und Abschnitt 12 „Zwei-Faktor-Anmeldung“ (ein Satz zur TOTP-Pflicht von unterwegs)
- Modify: `docs/superpowers/specs/2026-10-08-bindaems-design.md`: 1.4, E13, 14 „Zugriff“, neuer Absatz „Stand Plan Fernzugriff (23.11.2026)“ am Ende von 14
- Modify: `docs/uebergabe.md`: Kopfblock „Stand an der Anlage“ und Punkt 31 unter „Offen an der Anlage“
- Test: `tests/unit/test_docs.py` (vorhandene Prüfungen bleiben grün)

- [ ] **Step 1: Betriebshandbuch.** Der Unterabschnitt enthält: (a) die Tabelle der Kopfzeilen von oben; (b) die CA mit `client-ca.sh` (init offline auf dem Laptop, nicht auf dem HAProxy-Rechner; je Gerät `add`; `.p12` aufs Gerät; `ca.pem` und `crl.pem` auf den HAProxy); (c) dieses HAProxy-Beispiel, Variante „Heimnetz ohne Zertifikat“ (Entscheidung 1), mit `ems.example.at` als Platzhalter:

```haproxy
# crt-list.txt: eigener Eintrag nur für den EMS-Namen
#   /etc/haproxy/certs/ems.pem [verify optional ca-file /etc/haproxy/ems-client-ca.pem crl-file /etc/haproxy/ems-client-crl.pem] ems.example.at
#   (bei „jedes Gerät mit Zertifikat“: verify required)
frontend https
    bind :443 ssl crt-list /etc/haproxy/crt-list.txt alpn h2,http/1.1 ssl-min-ver TLSv1.2
    http-request del-header X-BindaEMS-Client-Cert
    http-request del-header X-BindaEMS-Client-CN
    use_backend bindaems if { req.hdr(host),field(1,:) -i ems.example.at }

backend bindaems
    acl ems_sni  ssl_fc_sni -i ems.example.at
    acl cert_used  ssl_c_used
    acl cert_valid ssl_c_verify 0
    acl heimnetz   src 192.168.1.0/24
    # andere TLS-Verbindung (anderer SNI, HTTP/2-Coalescing): Browser baut eine eigene auf
    http-request return status 421 if !ems_sni
    http-request deny deny_status 403 if !cert_used !heimnetz
    http-request deny deny_status 403 if cert_used !cert_valid
    http-request set-header X-BindaEMS-Client-Cert 1 if cert_used cert_valid
    http-request set-header X-BindaEMS-Client-CN %[ssl_c_s_dn(CN)] if cert_used cert_valid
    http-request set-header X-Forwarded-Proto https
    option forwardfor
    http-response set-header Strict-Transport-Security "max-age=31536000"
    timeout tunnel 1h          # Live-WebSocket /api/live
    option httpchk GET /health
    server ems <vm>:8080 check
```

dazu die Hinweise: eigenes Serverzertifikat für `ems.…` (kein Wildcard, sonst bündelt der Browser Verbindungen), CRL läuft nach 365 Tagen ab und HAProxy lehnt dann **jedes** Zertifikat ab (`client-ca.sh crl` und HAProxy neu laden, Erinnerung im Kalender), ein Widerruf wirkt erst nach Neuladen von HAProxy; Port 8080 weiter nur vom Proxy erreichbar (Abschnitt 6, `DOCKER-USER`); `app.client_cert` erst einschalten, wenn die Prüfungen unten bestehen; (d) die Prüfungen aus „An der Anlage“, Schritt A7, als Liste mit den `curl`-Befehlen.
- [ ] **Step 2: Spec.** 1.4: „Öffentlicher Internetzugang ohne Client-Zertifikat: von unterwegs über VPN oder über den Reverse Proxy mit mTLS (14).“ E13: „Fernzugriff über VPN oder Reverse Proxy mit Client-Zertifikat (mTLS) und Nachprüfung in der app; TOTP für Admins von unterwegs Pflicht“. 14 „Zugriff“: entsprechend. Absatz „Stand Plan Fernzugriff“: Kopfzeilen-Vertrag, Regel-Tabelle aus Task 2, TOTP-Regel aus Task 3, ab Werk aus, nur app.
- [ ] **Step 3: Übergabe.** Kopfblock: „**Plan Fernzugriff mit Client-Zertifikaten** … auf `main`, **nicht ausgerollt** (Punkt 31). Nur app, keine Migration; der core bleibt unverändert, ab Werk aus.“ Punkt 31: Reihenfolge A1 bis A8 unten, Verweis auf `docs/betrieb.md`, Abschnitt 6.
- [ ] **Step 4: Run** `uv run pytest tests/unit/test_docs.py tests/unit/test_deploy_files.py -q` → PASS.
- [ ] **Step 5: Commit** `docs: Fernzugriff mit Client-Zertifikaten`

---

## An der Anlage (Binda, nach Ausrollen; nichts davon schaltet einen Aktor)

- **A1 CA anlegen** (Laptop, offline genug): `client-ca.sh init`, dann je Gerät `client-ca.sh add handy-binda` usw. `CA_DIR` sichern (verschlüsselt), nicht auf den HAProxy-Rechner kopieren.
- **A2 Geräte:** `.p12` installieren. iPhone/iPad: per AirDrop oder Mail öffnen → Profil installieren (Einstellungen → Allgemein → VPN und Geräteverwaltung). Android: Einstellungen → Sicherheit → Verschlüsselung → Zertifikat installieren → „VPN- und App-Nutzerzertifikat“. Windows/macOS: Doppelklick (Firefox hat einen eigenen Zertifikatspeicher).
- **A3 DNS und Serverzertifikat** für `ems.<deine-domain>` (Let's Encrypt wie für deine anderen Dienste), Portfreigabe 443 auf HAProxy (falls nicht schon da).
- **A4 HAProxy:** `ems-client-ca.pem` (= `ca.pem`) und `ems-client-crl.pem` (= `crl.pem`) ablegen, Konfiguration nach Abschnitt 6, `haproxy -c -f …`, neu laden.
- **A5 Firewall:** Port 8080 der VM nur vom HAProxy (Proxmox-Firewall oder `DOCKER-USER`), wie bisher.
- **A6 Echte Adressen:** Vom Handy im Mobilnetz eine Seite öffnen; im HAProxy-Log und im Log der app (`Anmeldung erfolgreich … ip=`) muss die öffentliche Adresse stehen, keine 192.168.…. Steht dort die Router-Adresse, Variante „jedes Gerät mit Zertifikat“ nehmen (`verify required`, `exempt_networks: []`).
- **A7 Prüfen, noch mit `client_cert.required: false`:** (1) Browser ohne Zertifikat → TLS-Fehler bzw. 403; (2) mit Zertifikat → Anmeldung, Live-Werte auf der Übersicht (WebSocket), auch als App vom Home-Bildschirm; (3) widerrufenes Testgerät (`add test`, `revoke test`, HAProxy neu laden) → abgelehnt; (4) Umgehung: `curl -k --resolve andere.<domain>:443:<öffentliche-IP> -H 'Host: ems.<domain>' https://andere.<domain>/api/auth/me` → 421, nicht 401.
- **A8 Einschalten:** `config.yaml` → `app.client_cert.required: true`, `exempt_networks` nach A6; dann `docker compose restart ems-app` (gleiches Image, die app liest nur `config.yaml` neu; neue Images weiter nur core und app gemeinsam); danach A7 (1), (2) und (4) noch einmal und dazu: `curl https://ems.<domain>/api/state` ohne Zertifikat von außen → abgelehnt. Als Admin unterwegs ohne TOTP → „Admins melden sich von unterwegs nur mit TOTP an …“.

## Offene Fragen an Binda

1. Wo läuft HAProxy (OPNsense/pfSense-Plugin, eigene VM/LXC, Container) und welche Version (`haproxy -v`)? Bei OPNsense gebe ich die Einstellungen der Oberfläche statt `haproxy.cfg` an.
2. Ist HAProxy schon heute der Reverse Proxy des EMS im Heimnetz (die Adresse in `app.trusted_proxies`)? Laufen andere Dienste (Home Assistant, Grafana) über denselben Frontend auf 443?
3. Gibt es schon einen öffentlichen Namen und ein Let's-Encrypt-Zertifikat dafür (eigener Name für das EMS oder Wildcard)?
4. Welche Geräte sollen Zugriff bekommen (iPhone, Android, Laptop, Browser), und nutzt du das EMS als App vom Home-Bildschirm?
5. Heimnetz ohne Zertifikat (Entscheidung 1) oder jedes Gerät mit Zertifikat? Welche Netze zählen als Heimnetz?
6. Passen die Entscheidungen 2 (TOTP-Pflicht für Admins von unterwegs), 3 (CA-Skript im Repo) und 4 (WebSocket-Ersatz erst bei Bedarf)?
