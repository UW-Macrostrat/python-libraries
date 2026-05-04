from warnings import warn

import psycopg.sql as psql3
import psycopg2.sql as psql2


def update_legacy_identifier(identifier):
    """
    For backwards compatibility with current code, we need to map psycopg2 identifiers to their equivalents in psycopg3,
    while printing a warning that the mapping is deprecated.
    :param identifier:
    :return: psycopg3 equivalent of identifier
    """
    new_identifier = _map_psycopg2_identifier_to_psycopg3_identifier_internal(
        identifier
    )
    if new_identifier is not identifier:
        warn(
            "psycopg2 identifiers are deprecated. Please use psycopg3 identifiers instead.",
            DeprecationWarning,
        )
    return new_identifier


def _map_psycopg2_identifier_to_psycopg3_identifier_internal(identifier):
    if isinstance(identifier, psql2.Identifier):
        return psql3.Identifier(*identifier._wrapped)
    if isinstance(identifier, psql2.SQL):
        return psql3.SQL(identifier._wrapped)
    if isinstance(identifier, psql2.Literal):
        return psql3.Literal(identifier._wrapped)
    if isinstance(identifier, psql2.Placeholder):
        return psql3.Placeholder(identifier._obj)
    if isinstance(identifier, psql2.Composed):
        return psql3.Composed(identifier._obj)
    return identifier
