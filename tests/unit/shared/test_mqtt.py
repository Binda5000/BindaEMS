import ssl

from bindaems.shared.config import MqttConfig
from bindaems.shared.mqtt import build_tls_context


def test_tls_context_without_verification() -> None:
    ctx = build_tls_context(MqttConfig(host="cerbo.lan", tls=True, tls_verify=False))
    assert ctx is not None
    assert ctx.check_hostname is False and ctx.verify_mode == ssl.CERT_NONE


def test_tls_context_with_verification() -> None:
    ctx = build_tls_context(MqttConfig(host="cerbo.lan", tls=True))
    assert ctx is not None and ctx.verify_mode == ssl.CERT_REQUIRED


def test_no_tls_context_for_plain_mqtt() -> None:
    assert build_tls_context(MqttConfig(host="cerbo.lan", port=1883, tls=False)) is None
