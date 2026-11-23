"""Retrieval eval: does each query find its runbook? Reported separately for
queries worded like the runbooks and queries worded differently."""

import json

from eval_runner.scoring import Rate


def load_retrieval_queries(path):
    return json.loads(path.read_text())


def evaluate_retriever(retriever, queries):
    rows = []
    for query in queries:
        hits = retriever.search(query["query"], top_k=3)
        ranked_ids = [hit.runbook.runbook_id for hit in hits]
        rows.append({**query, "ranked": ranked_ids,
                     "hit_at_1": ranked_ids[:1] == [query["expected_runbook"]],
                     "hit_at_3": query["expected_runbook"] in ranked_ids})
    return rows


def summarize_retrieval(rows):
    def rate(selected, key):
        return Rate(sum(1 for row in selected if row[key]), len(selected))

    matching = [row for row in rows if not row["wording_mismatch"]]
    mismatching = [row for row in rows if row["wording_mismatch"]]
    return {
        "matching_hit_at_1": rate(matching, "hit_at_1"), "matching_hit_at_3": rate(matching, "hit_at_3"),
        "mismatch_hit_at_1": rate(mismatching, "hit_at_1"), "mismatch_hit_at_3": rate(mismatching, "hit_at_3"),
        "all_hit_at_1": rate(rows, "hit_at_1"), "all_hit_at_3": rate(rows, "hit_at_3"),
    }


def format_retrieval_report(retriever_name, rows):
    summary = summarize_retrieval(rows)
    lines = [f"RETRIEVAL ({retriever_name})", "",
             f"{'query':5}  {'mismatch':8}  {'expected':8}  ranked"]
    for row in rows:
        mark = "" if row["hit_at_1"] else ("  (top 3)" if row["hit_at_3"] else "  MISS")
        lines.append(f"{row['id']:5}  {str(row['wording_mismatch']):8}  "
                     f"{row['expected_runbook']:8}  {', '.join(row['ranked']) or '-'}{mark}")
    lines += ["",
              f"wording like the runbooks: hit@1 {summary['matching_hit_at_1'].text()}, "
              f"hit@3 {summary['matching_hit_at_3'].text()}",
              f"wording unlike the runbooks: hit@1 {summary['mismatch_hit_at_1'].text()}, "
              f"hit@3 {summary['mismatch_hit_at_3'].text()}",
              f"all: hit@1 {summary['all_hit_at_1'].text()}, hit@3 {summary['all_hit_at_3'].text()}"]
    return "\n".join(lines)


def format_retrieval_comparison(rows_by_name):
    """Hit rates of several retrievers on the same queries, side by side."""
    names = list(rows_by_name)
    summaries = {name: summarize_retrieval(rows) for name, rows in rows_by_name.items()}
    lines = ["RETRIEVAL COMPARISON (same queries)", "",
             f"{'queries':28}" + "".join(f"  {name:>16}" for name in names)]
    for label, key in (("like the runbooks, hit@1", "matching_hit_at_1"),
                       ("like the runbooks, hit@3", "matching_hit_at_3"),
                       ("unlike the runbooks, hit@1", "mismatch_hit_at_1"),
                       ("unlike the runbooks, hit@3", "mismatch_hit_at_3"),
                       ("all, hit@1", "all_hit_at_1"),
                       ("all, hit@3", "all_hit_at_3")):
        lines.append(f"{label:28}" + "".join(f"  {summaries[name][key].text():>16}" for name in names))
    return "\n".join(lines)
