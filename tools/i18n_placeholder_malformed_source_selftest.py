#!/usr/bin/env python3
"""Equally malformed source/translation percent syntax never self-certifies."""
import i18n_placeholder_check as gate


def main():
    for key, value in (("Hello %s %", "Hola %s %"),
                       ("Value %s %(", "Valor %s %(")):
        assert gate.check(key, value), (key, value)
    assert not gate.check("Done %s — 100%%", "Hecho %s — 100%%")
    print("PASS malformed percent syntax is rejected on both sides")
    print("PASS escaped literal percent remains valid")
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
