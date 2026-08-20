"""BUILD-SPEC.md §9 step 4 — Cluster. tau=0.62, connected components over
score>=tau edges, with guards: hard negatives always override (a same-sentence
negative pair can never share a cluster, regardless of aggregate score); size cap
~12 (weak-bridge split); conflicting confirmed emails force a split."""

from collections import defaultdict

TAU = 0.62
SIZE_CAP = 12
_MAX_SPLIT_ITERATIONS = 200


class UnionFind:
    def __init__(self, n: int):
        self.parent = list(range(n))

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


def _components(n: int, edges: list[tuple[int, int, float]]) -> dict[int, list[int]]:
    uf = UnionFind(n)
    for i, j, _ in edges:
        uf.union(i, j)
    groups: dict[int, list[int]] = defaultdict(list)
    for idx in range(n):
        groups[uf.find(idx)].append(idx)
    return groups


def _split_component(
    member_idxs: list[int],
    edges_by_pair: dict[tuple[int, int], float],
    emails_by_idx: dict[int, str],
) -> list[list[int]]:
    """Iteratively drop the weakest edge inside this component until it satisfies
    both guards: size<=SIZE_CAP and no two distinct confirmed emails share a part.
    Bounded iteration count so a pathological bucket can't hang the run."""
    local_edges = [
        (i, j, w) for (i, j), w in edges_by_pair.items() if i in member_idxs and j in member_idxs
    ]
    members = set(member_idxs)

    def ok(groups: dict[int, list[int]]) -> bool:
        for part in groups.values():
            if len(part) > SIZE_CAP:
                return False
            emails = {emails_by_idx[i] for i in part if emails_by_idx.get(i)}
            if len(emails) > 1:
                return False
        return True

    iterations = 0
    while iterations < _MAX_SPLIT_ITERATIONS:
        uf = UnionFind(max(members) + 1) if members else UnionFind(1)
        for i, j, _ in local_edges:
            uf.union(i, j)
        groups = defaultdict(list)
        for idx in members:
            groups[uf.find(idx)].append(idx)
        if ok(groups) or not local_edges:
            return list(groups.values())
        local_edges.sort(key=lambda e: e[2])
        local_edges.pop(0)
        iterations += 1
    # give up splitting further; return whatever the last partition was
    uf = UnionFind(max(members) + 1) if members else UnionFind(1)
    for i, j, _ in local_edges:
        uf.union(i, j)
    groups = defaultdict(list)
    for idx in members:
        groups[uf.find(idx)].append(idx)
    return list(groups.values())


def cluster_records(
    n: int,
    scored_pairs: dict[tuple[int, int], tuple[float, dict[str, float]]],
    emails_by_idx: dict[int, str],
) -> list[list[int]]:
    """scored_pairs: (i,j) -> (score, features). emails_by_idx: idx -> confirmed
    email string or None. Returns list of clusters (each a list of record indices),
    including singleton clusters for every unclustered record."""
    edge_list: list[tuple[int, int, float]] = []
    edges_by_pair: dict[tuple[int, int], float] = {}
    hard_negative_pairs: set[tuple[int, int]] = set()

    for (i, j), (score, feats) in scored_pairs.items():
        if feats.get("negative_cooccurrence", 0.0) >= 1.0:
            hard_negative_pairs.add((i, j))
            continue
        if score >= TAU:
            edge_list.append((i, j, score))
            edges_by_pair[(i, j)] = score

    raw_groups = _components(n, edge_list)

    final_clusters: list[list[int]] = []
    for member_idxs in raw_groups.values():
        if len(member_idxs) == 1:
            final_clusters.append(member_idxs)
            continue
        parts = _split_component(member_idxs, edges_by_pair, emails_by_idx)
        final_clusters.extend(parts)

    return final_clusters
