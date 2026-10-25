from . import jsonld


def scrape(shop: dict, session, probe: bool = False):
    # SSENSE pages use the same JSON-LD offers and retain each item's actual brand.
    return jsonld.scrape(shop, session, probe)
