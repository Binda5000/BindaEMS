# Fernzugriff über HAProxy mit Client-Zertifikaten Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Die Web-UI von BindaEMS ist von unterwegs über den vorhandenen HAProxy erreichbar, aber nur mit einem Client-Zertifikat aus Bindas eigener CA (mTLS); die app prüft das als zweite Linie selbst nach.

**Architecture:** HAProxy terminiert TLS, verlangt für den EMS-Hostnamen ein gültiges, nicht widerrufenes Client-Zertifikat (CA-Datei und CRL) und meldet das Ergebnis der app in zwei Kopfzeilen. Die app übernimmt diese Kopfzeilen nur von `app.trusted_proxies` und weist mit `app.client_cert.required` jede Anfrage ab, die weder ein geprüftes Zertifikat noch eine Adresse aus dem Heimnetz hat; das fängt Fehlkonfigurationen des Proxys ab (z. B. die SNI/Host-Umgehung). Anmeldung, Rollen, Sitzungen und CSRF bleiben wie sie sind; Admins brauchen von unterwegs TOTP. Nur app; der core bleibt unverändert, das UI auch.

**Tech Stack:** Python 3.12, FastAPI/Starlette, uvicorn (`ProxyHeadersMiddleware`), pydantic, pytest; an der Anlage HAProxy-Plugin der OPNsense mit CA, Client-Zertifikaten und CRL aus „System → Trust“.

**Spec:** `docs/superpowers/specs/2026-10-08-bindaems-design.md`, 1.4 „Nicht-Ziele“ („Öffentlicher Internetzugang: Zugriff von unterwegs läuft über VPN.“), Entscheidung E13 („Fernzugriff über VPN, TOTP optional“), 14 „Login und Anwendungssicherheit“ (Rollen, Sitzungen, „Vertrauen in `X-Forwarded-*` nur vom konfigurierten Reverse Proxy“, „Zugriff: HTTPS terminiert am Reverse Proxy; von außen nur über VPN.“), 15 „Deployment“. Betrieb: `docs/betrieb.md`, Abschnitt 6 „Reverse Proxy“. Neuer Absatz „Stand Plan Fernzugriff“ kommt mit Task 4.

## Befund

- **Spec heute:** Öffentlicher Zugriff ist Nicht-Ziel (1.4), E13 nennt als verworfene Alternative „öffentlicher Zugriff mit Pflicht-2FA“. Dieser Plan ändert das: öffentlich erreichbar über HAProxy, aber erst nach mTLS. Das Client-Zertifikat ist der Besitzfaktor (Gerät), das Passwort der Wissensfaktor. VPN bleibt als Weg erlaubt.
- **Die app ist auf einen Proxy vorbereitet:** `app.trusted_proxies` (uvicorn `proxy_headers`/`forwarded_allow_ips` in `runtime._serve_http`), `app.cookie_secure` (Standard `true`, Cookies `HttpOnly`, `Secure`, `SameSite=Strict`), CSRF per Double-Submit, CSP und weitere Security-Header (`api/server.py`), Sperre nach 5 Fehlversuchen in 15 min je Benutzer, optional TOTP. Der Live-WebSocket `/api/live` prüft `Origin` gegen `Host` und die Sitzung.
- **Was fehlt:** Die app weiß nicht, ob eine Anfrage mit Zertifikat kam. Prüft nur HAProxy, hängt die ganze Sicherheit an einer Zeile Proxy-Konfiguration. Bekannte Falle: Bei SNI-basierter Prüfung (`crt-list` mit `verify required` nur für `ems.…`) kann ein Angreifer die TLS-Verbindung mit dem SNI eines anderen Dienstes ohne Zertifikatspflicht aufbauen und dann `Host: ems.…` senden; HAProxy routet nach `Host` und die app ist ohne Zertifikat erreichbar. Dasselbe passiert ehrlichen Browsern durch HTTP/2-Connection-Coalescing (dann ohne Zertifikat statt mit).
- **Sperre als Angriffsfläche:** Öffentlich könnte jeder durch 5 falsche Passwörter das Admin-Konto 15 min sperren. Mit mTLS davor erreicht nur ein Gerät mit Zertifikat das Login; das Problem entfällt.
- **Geräte (Binda, 23.11.2026):** mehrere Android-Handys (EMS als App vom Home-Bildschirm) und ein MacBook. Die installierte App auf Android läuft über Chrome und nimmt das Zertifikat aus dem Android-Schlüsselspeicher; ob Chrome es auch für den Live-WebSocket `/api/live` der installierten App mitschickt, ist nicht belegt (WebKit hatte dafür einen Fehler, [WebKit 158345](https://bugs.webkit.org/show_bug.cgi?id=158345), der Safari auf dem Mac betreffen kann). Ohne `/api/live` zeigt das UI keine Live-Werte. Das prüfen wir an der Anlage (A6); ein Ersatz über Abfragen ist nicht Teil dieses Plans (Entscheidung 4). Firefox auf Android kann keine Client-Zertifikate aus dem System nutzen; dort Chrome nehmen.
- **HAProxy (Binda, 23.11.2026):** läuft als Plugin auf der OPNsense, ist heute schon der Reverse Proxy des EMS, aber nur intern erreichbar. Name und Serverzertifikat `ems.binders.co.at` gibt es schon. Das Plugin stellt die Prüfung des Client-Zertifikats je Public Service ein (Client Certificate Auth, Verification, Certificate Authorities, CRLs aus „System → Trust“), nicht je Hostname; eigene HAProxy-Zeilen gehen über „Option pass-through“.
- **Docker-Healthcheck** fragt `http://127.0.0.1:8080/health` ohne Proxy ab; `/health` muss ohne Zertifikat gehen (liefert nur Status und Version).

## Optionen

**A – HAProxy prüft, die app prüft nach (empfohlen, dieser Plan).** mTLS am HAProxy, dazu eine kleine Zugangsprüfung in der app (`app.client_cert.required`), ab Werk aus. Schützt gegen die SNI/Host-Umgehung und gegen einen vergessenen Schalter am Proxy, protokolliert das Gerät (CN) bei der Anmeldung und erlaubt die TOTP-Pflicht für Admins von unterwegs.

**B – Nur HAProxy, die app bleibt unverändert.** Kein Code, nur Doku und Spec. Jeder Fehler in der Proxy-Konfiguration öffnet die app direkt dem Internet, und die app kann extern und Heimnetz nicht unterscheiden (kein TOTP-Zwang, kein Gerät im Log).

**C – VPN statt öffentlichem Zugang (Spec heute).** Am sichersten, aber nicht, was du willst.

## Entscheidungen (1 von Binda am 23.11.2026 beantwortet; 2 bis 6 Vorschlag, wartet auf Freigabe)

1. **Heimnetz ohne Zertifikat (Binda, 23.11.2026: „nimm RFC 1918 Netze an“):** Anfragen, deren echte Client-Adresse (nach `X-Forwarded-For` vom vertrauten Proxy) in `10.0.0.0/8`, `172.16.0.0/12` oder `192.168.0.0/16` liegt, brauchen kein Zertifikat, so wie heute; in der app `app.client_cert.exempt_networks`, im HAProxy dieselben Netze. Alles andere braucht eins. Da HAProxy auf der OPNsense selbst läuft, sieht er die echten öffentlichen Adressen; das bestätigst du in A5.
2. **Admin von unterwegs nur mit TOTP:** Eine Anmeldung von außen (`remote`) eines Admins ohne eingerichtetes TOTP lehnt die app nach richtigem Passwort ab (403, „Admins melden sich von unterwegs nur mit TOTP an. Richte TOTP im Heimnetz unter „Konto“ ein.“). Lesen und Bedienen brauchen nur Passwort und Zertifikat. Eine im Heimnetz begonnene Admin-Sitzung endet wie bisher nach 30 min Leerlauf; sie wird unterwegs nicht zusätzlich gesperrt.
3. **CA, Client-Zertifikate und Sperrliste in der OPNsense** („System → Trust“), nicht per Skript im Repo: Das HAProxy-Plugin nimmt CA und CRL direkt von dort, ein Widerruf wirkt nach „Apply“ ohne Dateien zu kopieren, und die Lebensdauer der CRL stellst du dort lang genug ein. Ein CA-Skript im Repo entfällt deshalb.
4. **Kein Polling-Ersatz für den WebSocket jetzt:** Erst wenn A6 zeigt, dass ein Gerät den Live-WebSocket mit Zertifikat nicht aufbaut, gibt es dafür einen eigenen kleinen Plan.
5. **Spec:** 1.4 und E13 lassen öffentlichen Zugriff zu, aber nur hinter mTLS am Reverse Proxy plus Nachprüfung in der app; VPN bleibt erlaubt. 14 „Zugriff“ entsprechend.
6. **Ausrollen:** nur app, keine Migration, kein Schritt an der UI, der core bleibt unverändert. `client_cert.required` ist ab Werk aus, das Image ändert also ohne Eintrag in `config.yaml` nichts. Kann mit jedem gemeinsamen Pull nach Stufe 2 mit (core und app weiter immer gemeinsam mit fester Version). Erst einschalten, wenn A1 bis A6 an der Anlage bestanden sind.

## Kopfzeilen zwischen HAProxy und app (Vertrag)

| Kopfzeile | Wert | Wer setzt sie |
|---|---|---|
| `X-BindaEMS-Client-Cert` | `1`, nur wenn `ssl_c_used` und `ssl_c_verify` = 0 | HAProxy; vorher immer gelöscht |
| `X-BindaEMS-Client-CN` | CN des Zertifikats (Gerätename), höchstens 64 Zeichen | HAProxy; vorher immer gelöscht |
| `X-Forwarded-For`, `X-Forwarded-Proto` | wie heute (`option forwardfor`, `https`) | HAProxy |

Die app wertet beide `X-BindaEMS-*` nur aus, wenn die direkte Gegenstelle in `app.trusted_proxies` liegt; von jeder anderen Adresse ignoriert sie sie.

## Global Constraints

- Deutsch, du; Commits im Stil `feat(app): …`, `docs: …`; keine Modellbezeichnungen im Repo.
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

### Task 4: Betriebshandbuch, Spec und Übergabe

**Files:**
- Modify: `docs/betrieb.md`, Abschnitt 6 „Reverse Proxy“ (neuer Unterabschnitt „Fernzugriff mit Client-Zertifikaten (HAProxy)“) und Abschnitt 12 „Zwei-Faktor-Anmeldung“ (ein Satz zur TOTP-Pflicht von unterwegs)
- Modify: `docs/superpowers/specs/2026-10-08-bindaems-design.md`: 1.4, E13, 14 „Zugriff“, neuer Absatz „Stand Plan Fernzugriff (23.11.2026)“ am Ende von 14
- Modify: `docs/uebergabe.md`: Kopfblock „Stand an der Anlage“ und Punkt 31 unter „Offen an der Anlage“
- Test: `tests/unit/test_docs.py` (vorhandene Prüfungen bleiben grün)

- [ ] **Step 1: Betriebshandbuch.** Der Unterabschnitt „Fernzugriff mit Client-Zertifikaten (HAProxy)“ enthält: (a) die Tabelle der Kopfzeilen von oben; (b) die Einrichtung im HAProxy-Plugin der OPNsense (Schritte A1 bis A4 unten, allgemein formuliert, Hostname als Platzhalter `ems.example.at`); (c) diese Zeilen für „Option pass-through“ des Backends (gelten unabhängig davon, welcher Public Service die Verbindung angenommen hat):

```haproxy
http-request del-header X-BindaEMS-Client-Cert
http-request del-header X-BindaEMS-Client-CN
# andere TLS-Verbindung (anderer SNI, HTTP/2-Coalescing): der Browser baut eine eigene auf
http-request return status 421 if !{ ssl_fc_sni -i ems.example.at }
http-request deny deny_status 403 if !{ ssl_c_used } !{ src 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 }
http-request deny deny_status 403 if { ssl_c_used } !{ ssl_c_verify 0 }
http-request set-header X-BindaEMS-Client-Cert 1 if { ssl_c_used } { ssl_c_verify 0 }
http-request set-header X-BindaEMS-Client-CN %[ssl_c_s_dn(CN)] if { ssl_c_used } { ssl_c_verify 0 }
http-response set-header Strict-Transport-Security "max-age=31536000"
```

dazu die Hinweise: Im Public Service „Verification“ auf `optional` (Heimnetz ohne Zertifikat) bzw. `required` (jedes Gerät mit Zertifikat); CA **und** CRL dort auswählen (ohne CRL wirkt kein Widerruf, eine abgelaufene CRL lässt HAProxy **jedes** Zertifikat ablehnen); im Backend „X-Forwarded-For“ an, Tunnel-Timeout 1 h für den Live-WebSocket, Health-Check `GET /health`; teilt sich der Public Service 443 mit anderen Diensten, fragt der Browser eines Geräts mit EMS-Zertifikat auch dort nach einem Zertifikat (Auswahl „keins“ genügt); Port 8080 weiter nur vom Proxy erreichbar (Abschnitt 6, `DOCKER-USER`); `app.client_cert` erst einschalten, wenn die Prüfungen bestehen; (d) Import des `.p12` auf Android (Einstellungen → Sicherheit → Verschlüsselung und Anmeldedaten → Zertifikat installieren → „VPN- und App-Nutzerzertifikat“) und macOS (Doppelklick → Schlüsselbund „Anmeldung“), mit der Umwandlung, falls ein Gerät die Datei mit „falsches Passwort“ ablehnt: `openssl pkcs12 -in gerät.p12 -nodes -out gerät.pem && openssl pkcs12 -export -legacy -in gerät.pem -out gerät-legacy.p12` (danach `gerät.pem` löschen, es enthält den Schlüssel); (e) die Prüfungen aus A6 als Liste mit den `curl`-Befehlen.
- [ ] **Step 2: Spec.** 1.4: „Öffentlicher Internetzugang ohne Client-Zertifikat: von unterwegs über VPN oder über den Reverse Proxy mit mTLS (14).“ E13: „Fernzugriff über VPN oder Reverse Proxy mit Client-Zertifikat (mTLS) und Nachprüfung in der app; TOTP für Admins von unterwegs Pflicht“. 14 „Zugriff“: entsprechend. Absatz „Stand Plan Fernzugriff“: Kopfzeilen-Vertrag, Regel-Tabelle aus Task 2, TOTP-Regel aus Task 3, ab Werk aus, nur app.
- [ ] **Step 3: Übergabe.** Kopfblock: „**Plan Fernzugriff mit Client-Zertifikaten** … auf `main`, **nicht ausgerollt** (Punkt 31). Nur app, keine Migration; der core bleibt unverändert, ab Werk aus.“ Punkt 31: Reihenfolge A1 bis A7 unten, Verweis auf `docs/betrieb.md`, Abschnitt 6.
- [ ] **Step 4: Run** `uv run pytest tests/unit/test_docs.py tests/unit/test_deploy_files.py -q` → PASS.
- [ ] **Step 5: Commit** `docs: Fernzugriff mit Client-Zertifikaten`

---

## An der Anlage (Binda, nach Ausrollen; nichts davon schaltet einen Aktor)

- **A1 CA und Sperrliste in der OPNsense:** „System → Trust → Authorities“: neue interne CA, z. B. „BindaEMS Client-CA“ (EC P-256 oder RSA 2048, 10 Jahre). „System → Trust → Revocation“: interne CRL für diese CA anlegen, Lebensdauer lang genug (z. B. 3650 Tage).
- **A2 Ein Zertifikat je Gerät:** „System → Trust → Certificates“: Typ „Client“, ausgestellt von der Client-CA, CN = Gerätename (z. B. `handy-binda`, `macbook-binda`), 825 Tage. Als PKCS #12 mit Passwort herunterladen und auf das Gerät bringen (Import siehe Betriebshandbuch, Abschnitt 6). Ein verlorenes Gerät: dort widerrufen, dann im HAProxy „Apply“.
- **A3 HAProxy-Plugin, Public Service** (der vorhandene für `ems.binders.co.at`, zusätzlich auf der WAN-Adresse, Port 443): „Client Certificate Auth“ an, „Verification“ `optional`, „Certificate Authorities“ die Client-CA, „CRLs“ die CRL aus A1. Im Backend des EMS „X-Forwarded-For“ an, die Zeilen aus dem Betriebshandbuch, Abschnitt 6, unter „Option pass-through“ (mit `ems.binders.co.at`), Tunnel-Timeout 1 h. Firewall-Regel WAN → 443 zur OPNsense (falls noch nicht da). Apply.
- **A4 Firewall der VM:** Port 8080 weiter nur von der OPNsense (Proxmox-Firewall oder `DOCKER-USER`), wie bisher.
- **A5 Echte Adressen:** Vom Handy im Mobilnetz (WLAN aus) die Seite öffnen; im HAProxy-Log und im Log der app (`Anmeldung erfolgreich … ip=`) muss die öffentliche Adresse stehen, keine 10.…, 172.16–31.… oder 192.168.….
- **A6 Prüfen, noch mit `client_cert.required: false`:** (1) Handy ohne Zertifikat im Mobilnetz → abgelehnt (403); (2) mit Zertifikat → Zertifikat auswählen, Anmeldung, Live-Werte auf der Übersicht, **in der App vom Home-Bildschirm** und auf dem MacBook (Chrome und Safari); (3) Testzertifikat anlegen, widerrufen, Apply → abgelehnt; (4) Umgehung von außen: `curl -k --resolve andere.binders.co.at:443:<öffentliche-IP> -H 'Host: ems.binders.co.at' https://andere.binders.co.at/api/auth/me` → 421, nicht 401; (5) im Heimnetz ohne Zertifikat wie bisher.
- **A7 Einschalten:** `config.yaml` → `app.client_cert: {required: true, exempt_networks: [10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16]}`, dann `docker compose restart ems-app` (gleiches Image, die app liest nur `config.yaml` neu; neue Images weiter nur core und app gemeinsam); danach A6 (1), (2) und (5) noch einmal. Als Admin unterwegs ohne TOTP → „Admins melden sich von unterwegs nur mit TOTP an …“.

## Offene Fragen an Binda

1. Passen die Entscheidungen 2 (TOTP-Pflicht für Admins von unterwegs), 3 (CA in der OPNsense statt Skript) und 4 (WebSocket-Ersatz erst bei Bedarf)? Dann gib den Plan frei.
2. Läuft über denselben Public Service auf 443 schon ein anderer Dienst von außen? Dann fragen die Handys mit EMS-Zertifikat auch dort nach einem Zertifikat (harmlos, „keins“ wählen).
