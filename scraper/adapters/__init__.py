from . import burberry, jsonld, nike, sfcc, shopify, sitemap_jsonld, ssense, tiles

REGISTRY = {
    "shopify": shopify.scrape,
    "jsonld": jsonld.scrape,
    "burberry": burberry.scrape,
    "nike": nike.scrape,
    "sfcc": sfcc.scrape,
    "tiles": tiles.scrape,
    "ssense": ssense.scrape,
    "sitemap_jsonld": sitemap_jsonld.scrape,
}
