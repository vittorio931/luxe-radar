"""Vue compatible du catalogue dynamique LUXE RADAR."""

from .catalog import get_categories, get_site, get_sites


def get_marketplaces(include_planned=True):
    return get_sites() if include_planned else get_sites(status="active")


def get_marketplace(name):
    return get_site(name)


MARKETPLACES = get_marketplaces()
MARKETPLACE_GROUPS = get_categories()
