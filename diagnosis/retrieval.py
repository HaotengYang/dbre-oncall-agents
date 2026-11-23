"""Runbook retrieval for the SOP agent.

Retrieval is an interface (Retriever.search) so that implementations can be
compared. Only the keyword implementation exists (owner, 2026-11-21: keyword
first; vector only if time remains). It is BM25 over words, with no
dependencies and deterministic results.

Runbooks are ordinary Markdown files under runbooks/ with a small header (id,
title, causes). They are the team's own documents, not captured data.
"""

import math
import pathlib
import re
from collections import Counter
from dataclasses import dataclass

RUNBOOKS_DIRECTORY = pathlib.Path(__file__).resolve().parents[1] / "runbooks"
_WORD = re.compile(r"[a-z0-9]+")
_STOP_WORDS = frozenset(
    "a an and are as at be by for from has have in is it its of on or that the this to was "
    "were will with not no do does our we us you your they their there then than so if".split()
)


@dataclass(frozen=True)
class Runbook:
    runbook_id: str
    title: str
    causes: tuple
    text: str
    steps: tuple  # the numbered steps, in order


@dataclass(frozen=True)
class SearchHit:
    runbook: Runbook
    score: float


def _stem(word):
    """A tiny suffix stripper so "locks", "locked" and "locking" meet."""
    for suffix in ("ing", "ed", "es", "s"):
        if len(word) > len(suffix) + 2 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word


def tokenize(text):
    return [_stem(word) for word in _WORD.findall(text.lower()) if word not in _STOP_WORDS]


def parse_runbook(text):
    text = text.replace("\r\n", "\n")
    header, _, body = text.partition("\n---\n")
    fields = {}
    for line in header.strip("-\n").splitlines():
        name, _, value = line.partition(":")
        fields[name.strip()] = value.strip()
    steps = tuple(re.sub(r"^\d+\.\s*", "", line).strip()
                  for line in body.splitlines() if re.match(r"^\d+\.\s", line))
    return Runbook(
        runbook_id=fields["id"], title=fields["title"],
        causes=tuple(cause.strip() for cause in fields["causes"].split(",")),
        text=fields["title"] + "\n" + body, steps=steps,
    )


def load_runbooks(directory=RUNBOOKS_DIRECTORY):
    """Reads the runbooks named in runbooks/INDEX, in that order.

    An explicit index instead of a directory listing: the diagnosis side is
    not allowed to search the filesystem (the ground-truth isolation test
    forbids it), and an index also makes the corpus a deliberate choice.
    """
    names = [line.strip() for line in (directory / "INDEX").read_text().splitlines() if line.strip()]
    for name in names:
        if not re.fullmatch(r"[A-Za-z0-9_-]+\.md", name):
            raise ValueError(f"runbooks/INDEX may list plain .md file names only, got {name!r}")
    return tuple(parse_runbook((directory / name).read_text()) for name in names)


class Retriever:
    """The retrieval interface."""

    name = ""

    def search(self, query, top_k=3):
        raise NotImplementedError


class KeywordRetriever(Retriever):
    """BM25 with the usual parameters (k1 1.5, b 0.75)."""

    name = "keyword"
    k1 = 1.5
    b = 0.75

    def __init__(self, runbooks=None):
        self.runbooks = runbooks if runbooks is not None else load_runbooks()
        self._documents = [Counter(tokenize(runbook.text)) for runbook in self.runbooks]
        self._lengths = [sum(counts.values()) for counts in self._documents]
        self._average_length = sum(self._lengths) / max(len(self._lengths), 1)
        document_frequency = Counter()
        for counts in self._documents:
            document_frequency.update(counts.keys())
        total = len(self._documents)
        self._idf = {term: math.log(1 + (total - frequency + 0.5) / (frequency + 0.5))
                     for term, frequency in document_frequency.items()}

    def search(self, query, top_k=3):
        query_terms = tokenize(query)
        scored = []
        for runbook, counts, length in zip(self.runbooks, self._documents, self._lengths):
            score = 0.0
            for term in query_terms:
                frequency = counts.get(term, 0)
                if frequency:
                    score += self._idf[term] * frequency * (self.k1 + 1) / (
                        frequency + self.k1 * (1 - self.b + self.b * length / self._average_length))
            if score > 0:
                scored.append(SearchHit(runbook, score))
        scored.sort(key=lambda hit: (-hit.score, hit.runbook.runbook_id))
        return scored[:top_k]


# Plain words for each root cause, added to the query so that retrieval is
# steered by the diagnosis and not only by how the alert happens to be worded.
CAUSE_QUERY_WORDS = {
    "missing_index_slow_query": "missing index slow query rows examined full scan EXPLAIN",
    "lock_wait": "lock wait blocked sessions transaction holding locks",
    "connection_exhaustion": "too many connections refused connection count pool",
    "healthy": "",
    "undetermined": "",
}


def recommend_steps(diagnosis, alert_text, retriever=None):
    """The SOP agent: steps quoted from the best-matching runbook, with the
    runbook named. Nothing is recommended for healthy or undetermined: there
    is no runbook for "we do not know yet"."""
    if diagnosis.root_cause in ("healthy", "undetermined"):
        return ()
    retriever = retriever or KeywordRetriever()
    query = " ".join([CAUSE_QUERY_WORDS.get(diagnosis.root_cause, ""), alert_text,
                      " ".join(diagnosis.symptoms)])
    # Only runbooks written for the diagnosed cause are eligible: steps for a
    # different cause would contradict the report's own conclusion.
    hits = [hit for hit in retriever.search(query, top_k=len(getattr(retriever, "runbooks", ())) or 10)
            if diagnosis.root_cause in hit.runbook.causes]
    if not hits:
        return ()
    runbook = hits[0].runbook
    return tuple(f"[{runbook.runbook_id} {runbook.title}] {step}" for step in runbook.steps)
