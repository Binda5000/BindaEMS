import pytest

from bindaems.app.settings.service import SettingsService


@pytest.fixture
def service(settings_service: SettingsService) -> SettingsService:
    return settings_service
