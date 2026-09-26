"""Latency of uncached calls to the diagnostic.ar model, for the per-round cost.

    python -m eval.model_latency [--calls 30]

Sends each TEST case's reported findings plus one random extra term (so the
query is new to any server-side cache), one call at a time, and prints the
median and p90 in milliseconds.
"""

from __future__ import annotations

import argparse
import random
import statistics
import time
from collections.abc import Sequence

from eval.cases import load
from eval.harness import MODEL_URL, TOP_K, open_kb

from diagnosticar_preguntas.ranking.model_client import HttpModelClient


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--calls", type=int, default=30)
    args = parser.parse_args(argv)

    kb = open_kb()
    terms = sorted(t for t in kb.term_ids() if kb.is_phenotypic_abnormality(t))
    rng = random.Random(7)
    timings = []
    with HttpModelClient(MODEL_URL, timeout=120.0) as client:
        for case in load("test")[: args.calls]:
            query = sorted({*case.present, rng.choice(terms)})
            started = time.perf_counter()
            client.rank(query, TOP_K)
            timings.append((time.perf_counter() - started) * 1000)
    timings.sort()
    p90 = timings[int(0.9 * (len(timings) - 1))]
    print(f"{len(timings)} calls: median {statistics.median(timings):.0f} ms, p90 {p90:.0f} ms")


if __name__ == "__main__":
    main()
