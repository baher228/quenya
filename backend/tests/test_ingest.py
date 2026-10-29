import json

from app import ingest


def _record(item_id: str, query: str) -> dict:
    return {
        "id": item_id,
        "source": "vinted",
        "source_url": f"https://vinted.example/{item_id}",
        "title": query,
        "image_url": f"https://images.example/{item_id}.jpg",
    }


def test_select_seeds_samples_requested_count_without_duplicates(monkeypatch):
    seeds = [(f"m{i}", f"query-{i}") for i in range(5)]
    monkeypatch.setattr(ingest.random, "sample", lambda values, count: values[:count])

    assert ingest.select_seeds(seeds, 3) == seeds[:3]
    assert ingest.select_seeds(seeds, 10) is seeds
    assert ingest.select_seeds(seeds) is seeds


def test_ingest_tags_new_items_embeds_and_continues_after_query_error(monkeypatch):
    records = [_record("new-item", "band tee")]
    calls = {"tag": None, "tag_kwargs": None, "embed": None}
    monkeypatch.setattr(
        ingest,
        "read_seeds",
        lambda: [("m", "band tee"), ("w", "broken query")],
    )
    monkeypatch.setattr(
        ingest.db,
        "init_db",
        lambda: None,
    )
    monkeypatch.setattr(
        ingest.db,
        "source_urls",
        lambda: {"https://vinted.example/existing"},
    )
    monkeypatch.setattr(
        ingest.db,
        "insert_items",
        lambda inserted: len(inserted),
    )

    def search(query, gender, per_query):
        expected_gender = "m" if query == "band tee" else "w"
        assert gender == expected_gender
        if query == "broken query":
            raise RuntimeError("temporary search failure")
        assert per_query == 7
        return records

    monkeypatch.setattr(ingest.vinted, "search", search)

    def tag_untagged(*args, **kwargs):
        calls["tag"] = args
        calls["tag_kwargs"] = kwargs
        return {"selected": 1, "ok": 1, "failed": 0}

    monkeypatch.setattr(ingest.tag, "tag_untagged", tag_untagged)
    monkeypatch.setattr(
        ingest.embed,
        "backfill",
        lambda batch_size, *, limit: calls.__setitem__(
            "embed", (batch_size, limit)
        ),
    )

    summary = ingest.ingest(
        "vinted",
        7,
        run_tag=True,
        run_embed=True,
        embed_batch_size=8,
        embed_limit=37,
    )

    assert summary["queries"] == 2
    assert summary["records"] == 1
    assert summary["inserted"] == 1
    assert summary["query_errors"] == 1
    assert calls["tag"] == (1, 2)
    assert calls["tag_kwargs"] == {
        "source": "vinted",
        "item_ids": ["new-item"],
    }
    assert calls["embed"] == (8, 37)


def test_main_prints_one_compact_json_summary_line(monkeypatch, capsys):
    calls = {}

    def ingest_stub(*args, **kwargs):
        calls["args"] = args
        calls["kwargs"] = kwargs
        return {"source": args[0], "queries": 2}

    monkeypatch.setattr(
        ingest,
        "ingest",
        ingest_stub,
    )
    monkeypatch.setattr(
        ingest.sys,
        "argv",
        ["ingest", "--source", "retail", "--sample", "2", "--tag"],
    )

    ingest.main()

    output = capsys.readouterr()
    assert output.err == ""
    assert output.out.count("\n") == 1
    assert json.loads(output.out) == {"source": "retail", "queries": 2}
    assert calls["kwargs"]["embed_limit"] == 2000
