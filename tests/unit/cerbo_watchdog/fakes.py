from collections.abc import Iterable


class DbusArray(list[object]):
    """Wie ``dbus.Array`` von dbus-python: eine Liste, die ihre Signatur kennt.

    Venus OS meldet einen ungültigen Wert als leeres Array.
    """

    def __init__(self, items: Iterable[object] = (), signature: str = "i") -> None:
        super().__init__(items)
        self.signature = signature


class FakeAccess:
    def __init__(
        self,
        values: dict[tuple[str, str], object],
        services: dict[tuple[str, int], str] | None = None,
        failing_sets: int = 0,
    ) -> None:
        self.values, self.services, self.failing_sets = dict(values), services or {}, failing_sets
        self.writes: list[tuple[str, str, object]] = []

    def find_service(self, service: str, instance: int | None) -> str | None:
        return self.services.get((service, instance)) if instance is not None else service

    def get_value(self, service: str, path: str) -> object:
        return self.values.get((service, path))

    def set_value(self, service: str, path: str, value: object) -> bool:
        self.writes.append((service, path, value))
        if self.failing_sets > 0:
            self.failing_sets -= 1
            return False
        self.values[(service, path)] = value
        return True
