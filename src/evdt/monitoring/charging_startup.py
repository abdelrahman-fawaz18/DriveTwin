"""Working-time release gate for local Charging Advisor sessions."""

import os


def charging_startup_delay(value):
    if type(value) is not int or value not in (0, 30, 60, 120):
        raise ValueError("Charging Advisor delay must be 0, 30, 60, or 120 working seconds")
    return value


def startup_waiting(delay, working_s):
    return delay > 0 and (working_s is None or working_s < delay)


def configured_delay():
    return charging_startup_delay(int(os.environ.get("EVDT_CHARGING_STARTUP_DELAY_WORKING_S", "0")))
