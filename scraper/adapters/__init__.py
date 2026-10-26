from . import burberry, jsonld, nike, sfcc, shopify, ssense, tiles

REGISTRY = {
    "shopify": shopify.scrape,
    "jsonld": jsonld.scrape,
    "burberry": burberry.scrape,
    "nike": nike.scrape,
    "sfcc": sfcc.scrape,
    "tiles": tiles.scrape,
    "ssense": ssense.scrape,
}
