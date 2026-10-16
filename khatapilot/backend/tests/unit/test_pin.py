from datetime import timedelta

import pytest

from app.guard.pin import PinPolicyError, validate_new_pin
from tests.guard_harness import PIN


@pytest.mark.parametrize("weak", ["1111", "1234", "4321", "0123", "12a4", "123", "1234567"])
def test_weak_or_malformed_pins_are_refused(weak):
    with pytest.raises(PinPolicyError):
        validate_new_pin(weak)


def test_bangla_digits_are_accepted():
    assert validate_new_pin("৭৩৯১") == "7391"


def test_the_pin_is_stored_only_as_an_argon2_hash(world):
    with world.guard() as guard:
        stored = guard.store.security().pin_hash
    assert stored.startswith("$argon2id$")
    assert PIN not in stored


def test_right_pin_resets_the_counter(world):
    with world.guard() as guard:
        assert guard.pins.verify("0000").attempts_left == 2
        assert guard.pins.verify("0000").attempts_left == 1
        assert guard.pins.verify(PIN).ok
        assert guard.pins.verify("0000").attempts_left == 2


def test_three_wrong_pins_lock_for_fifteen_minutes(world):
    with world.guard() as guard:
        for _ in range(2):
            assert not guard.pins.verify("0000").locked
        third = guard.pins.verify("0000")
        assert third.locked and "locked" in third.message
        assert guard.pins.verify(PIN).locked, "even the right PIN is refused while locked"
    world.clock.advance(timedelta(minutes=14))
    with world.guard() as guard:
        assert guard.pins.lock_state()[0]
    world.clock.advance(timedelta(minutes=1, seconds=1))
    with world.guard() as guard:
        assert not guard.pins.lock_state()[0]
        assert guard.pins.verify(PIN).ok
