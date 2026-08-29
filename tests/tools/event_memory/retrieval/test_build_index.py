from types import SimpleNamespace

import pytest

from tools.event_memory.retrieval import build_index


class _Helpers:
    @staticmethod
    def event_memory_retrieval_text(event):
        return event["event_id"]

    @staticmethod
    def build_corpus_fingerprint(_corpus, _events, _doc_text):
        return "fingerprint"


def test_build_index_keeps_existing_file_when_any_embedding_fails(monkeypatch, tmp_path):
    output = tmp_path / "event_memory_embeddings.npz"
    output.write_bytes(b"existing-index")
    monkeypatch.setattr(build_index, "OUTPUT_PATH", output)
    monkeypatch.setattr(build_index, "_load_helpers", lambda: _Helpers)

    def create(**_kwargs):
        return SimpleNamespace(data=[SimpleNamespace(embedding=[1.0, 0.0])])

    client = SimpleNamespace(embeddings=SimpleNamespace(create=create))
    calls = 0

    def _create(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("temporary failure")
        return create(**kwargs)

    client.embeddings.create = _create

    with pytest.raises(RuntimeError, match="成功 1/2"):
        build_index.build_index([{"event_id": "one"}, {"event_id": "two"}], client)

    assert output.read_bytes() == b"existing-index"
    assert not output.with_suffix(".tmp.npz").exists()
