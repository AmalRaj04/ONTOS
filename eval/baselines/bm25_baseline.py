"""BUILD-SPEC.md §12 — BM25 baseline for comparison against the ONTOS graph
pipeline. vendor/EnterpriseRAG-Bench's own baseline
(src/scripts/answer_generation/bm25_retrieval.py) needs a running OpenSearch
server indexing the full corpus — standing that up plus indexing 511K documents
wasn't in reach inside this build's remaining time. This reimplements the same
idea (BM25 retrieval -> top-k docs -> LLM answer generation, no graph, no entity
resolution, no claims) with `rank_bm25` in-process instead.

Corpus scope: the question-priority tier (812 docs) plus the same stratified
Tier 2 sample src/ingest/run_tier2.py used (checkpointed under
data/checkpoints/tier2_done/) — not the full 244,822-doc ingested corpus. That
corpus is too large to safely hold in memory for an in-process BM25 index on
this build machine (PROJECT.md decision #37 — an 8GB-RAM machine already hit a
real OOM/swap-thrashing incident during ingest from a similarly-sized
miscalculation). This keeps the comparison to the same document neighborhood
ONTOS's Tier 2/conflict/eval layers reason over, which is a fair, real, if
bounded, apples-to-apples baseline — not a full-corpus one, noted here rather
than implied.

Usage: python -m eval.baselines.bm25_baseline
       EVAL_LIMIT=150 python -m eval.baselines.bm25_baseline
"""

import json
import os
import re
import time

from dotenv import load_dotenv
from rank_bm25 import BM25Okapi

from src.ingest.adapters import ALL_ADAPTERS
from src.ingest.priority import load_priority_documents
from src.ingest.run_tier2 import _load_done as _tier2_done
from src.llm.router import LLMRouter
from src.schema.models import Document

OUT_PATH = "eval/results/answers_bm25.jsonl"
_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")

_ANSWER_PROMPT = """Answer the question using ONLY the documents provided below. \
If the documents don't contain the answer, say so plainly. Return JSON:
{{"answer": str}}

QUESTION: {question}

DOCUMENTS:
{context}
"""


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def build_corpus(corpus_dir: str) -> list[Document]:
    docs: dict[str, Document] = {}
    for d in load_priority_documents(corpus_dir):
        docs[d.doc_id] = d
    for source, adapter_cls in ALL_ADAPTERS.items():
        done_ids = _tier2_done(source)
        if not done_ids:
            continue
        adapter = adapter_cls()
        for d in adapter.iter_documents(corpus_dir):
            if d.native_id in done_ids or d.doc_id in done_ids:
                docs[d.doc_id] = d
    return list(docs.values())


def main() -> None:
    load_dotenv()
    corpus_dir = os.environ["CORPUS_DIR"]
    limit = int(os.environ["EVAL_LIMIT"]) if os.environ.get("EVAL_LIMIT") else None
    top_k = int(os.environ.get("BM25_TOP_K", "5"))

    t0 = time.monotonic()
    print("[bm25] building corpus...", flush=True)
    docs = build_corpus(corpus_dir)
    print(f"[bm25] {len(docs)} documents ({time.monotonic()-t0:.0f}s)", flush=True)

    tokenized = [_tokenize(d.body) for d in docs]
    bm25 = BM25Okapi(tokenized)
    print(f"[bm25] index built ({time.monotonic()-t0:.0f}s)", flush=True)

    from eval.run_eval import load_questions

    questions = load_questions(limit, include_extra=False)
    router = LLMRouter()

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    n = 0
    with open(OUT_PATH, "w") as out:
        for q in questions:
            scores = bm25.get_scores(_tokenize(q["question"]))
            top_idx = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:top_k]
            top_docs = [docs[i] for i in top_idx if scores[i] > 0]

            context = "\n\n".join(f"[{d.native_id}] {d.title or ''}\n{d.body[:2000]}" for d in top_docs)
            answer = ""
            if context:
                try:
                    prompt = _ANSWER_PROMPT.format(question=q["question"], context=context)
                    result = router.complete(prompt, task="bm25_answer_generation")
                    answer = result.get("answer", "")
                except Exception as e:
                    print(f"[bm25] FAILED {q['question_id']}: {e}", flush=True)

            row = {
                "question_id": q["question_id"],
                "answer": answer,
                "document_ids": [d.native_id for d in top_docs],
                "abstained": not bool(context),
            }
            out.write(json.dumps(row) + "\n")
            out.flush()
            n += 1
            if n % 20 == 0:
                print(f"[bm25] {n}/{len(questions)} ({time.monotonic()-t0:.0f}s)", flush=True)

    print(f"[bm25] DONE {n} answered -> {OUT_PATH} ({time.monotonic()-t0:.0f}s)", flush=True)


if __name__ == "__main__":
    main()
