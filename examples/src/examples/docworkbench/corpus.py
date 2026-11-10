"""Corpus / split_corpus — 语料容器与按能力维度分层切分（spec §5.7）。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass
class CorpusPart:
    tasks: list[dict]
    ground_truth: dict
    documents: list[dict]


@dataclass
class Corpus:
    documents: list[dict]
    tasks: list[dict]
    ground_truth: dict
    acceptance_scripts: dict[str, Callable[[dict], dict]] = field(default_factory=dict)
    assets: dict[str, bytes] = field(default_factory=dict)  # doc_id -> PNG bytes

    def part(self, task_ids: list[str]) -> CorpusPart:
        return CorpusPart(
            tasks=[t for t in self.tasks if t["id"] in set(task_ids)],
            ground_truth={k: v for k, v in self.ground_truth.items() if k in set(task_ids)},
            documents=self.documents,
        )


def split_corpus(corpus: Corpus, practice_ratio: float) -> tuple[CorpusPart, CorpusPart]:
    """按能力维度分层切分：每个维度内部按 ratio 分，练习/保留两侧维度均衡。"""
    by_dim: dict[str, list[dict]] = {}
    for t in corpus.tasks:
        by_dim.setdefault(t["dimension"], []).append(t)

    practice_ids: list[str] = []
    heldout_ids: list[str] = []
    for _dim, tasks in sorted(by_dim.items()):
        cut = max(1, round(len(tasks) * practice_ratio))
        practice_ids.extend(t["id"] for t in tasks[:cut])
        heldout_ids.extend(t["id"] for t in tasks[cut:])

    return corpus.part(practice_ids), corpus.part(heldout_ids)
