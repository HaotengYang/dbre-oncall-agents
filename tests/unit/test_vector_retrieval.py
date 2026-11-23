"""Vector retrieval with a stand-in embedder (no model, no onnxruntime)."""

import pytest

from diagnosis.retrieval import Runbook
from diagnosis.vector_retrieval import EmbeddingModelMissing, MiniLMEmbedder, VectorRetriever
from eval_runner.retrieval_eval import evaluate_retriever, format_retrieval_comparison


class _WordAxisEmbedder:
    """One axis per known word, so similarity is predictable."""
    axes = ("lock", "connection", "index")

    def embed(self, texts):
        import math
        vectors = []
        for text in texts:
            vector = [float(text.lower().count(axis)) for axis in self.axes]
            norm = math.sqrt(sum(value * value for value in vector)) or 1.0
            vectors.append([value / norm for value in vector])
        return _Matrix(vectors)


class _Matrix(list):
    def __matmul__(self, other):
        return [sum(a * b for a, b in zip(row, other)) for row in self]


RUNBOOKS = (
    Runbook("RB01", "Missing index", ("missing_index_slow_query",), "index index scan", ()),
    Runbook("RB02", "Lock waits", ("lock_wait",), "lock lock blocked", ()),
    Runbook("RB04", "Connections", ("connection_exhaustion",), "connection refused", ()),
)


def test_runbooks_are_ranked_by_cosine_similarity():
    retriever = VectorRetriever(runbooks=RUNBOOKS, embedder=_WordAxisEmbedder())
    hits = retriever.search("sessions stuck on a lock", top_k=3)
    assert hits[0].runbook.runbook_id == "RB02"
    assert hits[0].score == pytest.approx(1.0)
    assert len(retriever.search("lock", top_k=2)) == 2


def test_ties_are_broken_by_runbook_id_so_results_are_deterministic():
    retriever = VectorRetriever(runbooks=RUNBOOKS, embedder=_WordAxisEmbedder())
    assert [hit.runbook.runbook_id for hit in retriever.search("nothing known")] == ["RB01", "RB02", "RB04"]


def test_a_missing_model_is_reported_with_the_command_that_fetches_it(tmp_path):
    with pytest.raises(EmbeddingModelMissing, match="fetch_embedding_model"):
        MiniLMEmbedder(model_directory=tmp_path)


def test_the_comparison_shows_both_retrievers_on_the_same_queries():
    queries = [{"id": "m01", "query": "a lock", "expected_runbook": "RB02", "wording_mismatch": True},
               {"id": "q01", "query": "index", "expected_runbook": "RB01", "wording_mismatch": False}]
    retriever = VectorRetriever(runbooks=RUNBOOKS, embedder=_WordAxisEmbedder())
    rows = evaluate_retriever(retriever, queries)
    text = format_retrieval_comparison({"keyword": rows, "vector": rows})
    assert "unlike the runbooks, hit@1" in text
    assert text.count("100.0% (1/1)") >= 4
