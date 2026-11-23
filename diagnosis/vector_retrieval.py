"""Vector retrieval: the second implementation of the Retriever interface.

Optional. It needs `requirements-vector.txt` and the model fetched by
`scripts/fetch_embedding_model.py` (all-MiniLM-L6-v2, run locally with
onnxruntime; no API call). Keyword retrieval stays the default.

Each runbook is embedded once (title and body); a query is embedded and the
runbooks are ranked by cosine similarity.
"""

import pathlib

from diagnosis.retrieval import Retriever, SearchHit, load_runbooks

MODEL_DIRECTORY = pathlib.Path(__file__).resolve().parents[1] / "models" / "all-MiniLM-L6-v2"
MAX_TOKENS = 256  # the model was trained on inputs of at most 256 word pieces


class EmbeddingModelMissing(RuntimeError):
    pass


class MiniLMEmbedder:
    """Mean-pooled, L2-normalised sentence embeddings from the ONNX model."""

    def __init__(self, model_directory=MODEL_DIRECTORY):
        try:
            import numpy
            import onnxruntime
            from tokenizers import Tokenizer
        except ImportError as missing:
            raise EmbeddingModelMissing(
                "vector retrieval needs: pip install -r requirements-vector.txt") from missing
        model_path = model_directory / "model.onnx"
        tokenizer_path = model_directory / "tokenizer.json"
        if not model_path.exists() or not tokenizer_path.exists():
            raise EmbeddingModelMissing(
                "embedding model not found; run: python scripts/fetch_embedding_model.py")
        self._numpy = numpy
        self._tokenizer = Tokenizer.from_file(str(tokenizer_path))
        self._tokenizer.enable_truncation(max_length=MAX_TOKENS)
        self._tokenizer.enable_padding()
        self._session = onnxruntime.InferenceSession(
            str(model_path), providers=["CPUExecutionProvider"])
        self._input_names = {model_input.name for model_input in self._session.get_inputs()}

    def embed(self, texts):
        numpy = self._numpy
        encodings = self._tokenizer.encode_batch(list(texts))
        input_ids = numpy.array([encoding.ids for encoding in encodings], dtype=numpy.int64)
        attention_mask = numpy.array([encoding.attention_mask for encoding in encodings],
                                     dtype=numpy.int64)
        feeds = {"input_ids": input_ids, "attention_mask": attention_mask}
        if "token_type_ids" in self._input_names:
            feeds["token_type_ids"] = numpy.zeros_like(input_ids)
        token_embeddings = self._session.run(None, feeds)[0]
        mask = attention_mask[:, :, None].astype(numpy.float32)
        pooled = (token_embeddings * mask).sum(axis=1) / numpy.clip(mask.sum(axis=1), 1e-9, None)
        norms = numpy.linalg.norm(pooled, axis=1, keepdims=True)
        return pooled / numpy.clip(norms, 1e-12, None)


class VectorRetriever(Retriever):
    name = "vector"

    def __init__(self, runbooks=None, embedder=None):
        self.runbooks = runbooks if runbooks is not None else load_runbooks()
        self._embedder = embedder or MiniLMEmbedder()
        self._runbook_vectors = self._embedder.embed([runbook.text for runbook in self.runbooks])

    def search(self, query, top_k=3):
        query_vector = self._embedder.embed([query])[0]
        similarities = self._runbook_vectors @ query_vector
        scored = [SearchHit(runbook, float(similarity))
                  for runbook, similarity in zip(self.runbooks, similarities)]
        scored.sort(key=lambda hit: (-hit.score, hit.runbook.runbook_id))
        return scored[:top_k]
