create schema if not exists quenya;

create table if not exists quenya.listings (
    url text primary key,
    source text not null,
    title text not null,
    brand text,
    price_gbp numeric(10,2) not null,
    gender text,
    category text,
    condition text default 'new',
    image_path text,
    image_url_original text,
    record jsonb not null,
    scraped_at timestamptz default now(),
    imported_at timestamptz
);

create index if not exists listings_scraped_at_idx
    on quenya.listings (scraped_at);
