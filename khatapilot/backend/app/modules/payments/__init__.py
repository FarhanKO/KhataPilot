"""Payments made through KhataPilot: records, schedules and execution against the provider.

Execution requires an `AuthorizedAction` from the guard that binds the exact parameters; the
module re-checks that binding before it calls the provider, and the provider checks it again.
"""
