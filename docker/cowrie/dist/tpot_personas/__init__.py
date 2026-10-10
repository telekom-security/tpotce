"""T-Pot Cowrie personas: catalog, identity, rendering and validation.

Standard library only: it runs in the Cowrie image (build and start) and in
the unit tests of docker/cowrie/tests on the host.
"""


class PersonaError(Exception):
    """A persona cannot be selected, rendered or does not pass validation."""
