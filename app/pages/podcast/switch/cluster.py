#!/usr/bin/env python3
"""
Voice prints (voice_worker.py) grouped into voices. numpy only - no scipy
or scikit-learn in Buddy - and no Qt, no Resolve.

    group(prints, voices=None) -> (labels, sizes)

Average-linkage agglomerative clustering on cosine similarity, the way
speaker diarization is usually done with these models: every print starts
as its own group and the two most alike groups merge, again and again,
until `voices` groups are left. With voices None ("Auto") the tree is
cut where the voices come out clearest (the best silhouette), and if no
cut is clearer than CLEAR, it's one voice. An hour is ~4800 prints,
too many to merge one by one (it's cubic), so at most MOST_MERGED of them,
spread evenly through the recording, are merged; every print then goes to
the voice its print is nearest, and the voices' averages are worked out
again from all of them (one k-means step). A group with under LEAST_SHARE
of the prints (a laugh, a cough, a door) joins the voice nearest it.

Labels are numbered by how much each voice talks: 0 the most.
"""

import numpy as np

CLEAR = 0.15            # Auto: the least silhouette that counts as two or more voices
MOST_MERGED = 900
LEAST_SHARE = 0.03
LEAST_PRINTS = 4
MOST_VOICES = 8
TIE = 0.03


def _unit(prints):
    prints = np.asarray(prints, dtype=np.float32)
    norm = np.linalg.norm(prints, axis=1, keepdims=True)
    return prints / np.maximum(norm, 1e-9)


def agglomerate(unit, most=MOST_VOICES):
    """{k: labels (0..k-1)} for every k from 1 to `most`: unit-length
    prints merged, the two most alike groups first, all the way down to
    one group - each cut of that one tree."""
    n = len(unit)
    if n == 0:
        return {}
    sim = unit @ unit.T
    np.fill_diagonal(sim, -np.inf)
    sizes = np.ones(n)
    owner = np.arange(n)
    cuts = {}
    groups = n
    while True:
        if groups <= most:
            cuts[groups] = np.unique(owner, return_inverse=True)[1]
        if groups == 1:
            break
        i, j = divmod(int(np.argmax(sim)), n)
        if not np.isfinite(sim[i, j]):
            break
        # Lance-Williams for average linkage: the merged group's similarity to
        # each other one is the size-weighted mean of the two it came from.
        merged = (sizes[i] * sim[i] + sizes[j] * sim[j]) / (sizes[i] + sizes[j])
        sim[i, :] = merged
        sim[:, i] = merged
        sim[i, i] = -np.inf
        sim[j, :] = -np.inf
        sim[:, j] = -np.inf
        sizes[i] += sizes[j]
        owner[owner == j] = i
        groups -= 1
    return cuts


def silhouette(unit, labels):
    """Mean silhouette of a grouping, on cosine distance: near 1 when every
    print is far nearer its own voice than the next; 0 or less, no better
    than chance. -1 for a single group (nothing to compare)."""
    k = int(labels.max()) + 1 if len(labels) else 0
    if k < 2:
        return -1.0
    dist = 1.0 - unit @ unit.T
    counts = np.bincount(labels, minlength=k).astype(float)
    sums = np.stack([dist[:, labels == c].sum(axis=1) for c in range(k)], axis=1)
    own = counts[labels]
    a = (sums[np.arange(len(labels)), labels]) / np.maximum(own - 1, 1)
    other = sums / np.maximum(counts, 1)
    other[np.arange(len(labels)), labels] = np.inf
    b = other.min(axis=1)
    s = np.where(own > 1, (b - a) / np.maximum(np.maximum(a, b), 1e-9), 0.0)
    return float(s.mean())


def _means(unit, labels, k):
    return _unit(np.array([unit[labels == c].mean(axis=0) if np.any(labels == c) else np.zeros(unit.shape[1])
                           for c in range(k)]))


def group(prints, voices=None, same=CLEAR):
    """(labels, sizes): each print's voice (0 = the one heard most), and
    how many prints each voice has. voices: how many there are, or None
    to find out."""
    unit = _unit(prints)
    n = len(unit)
    if n == 0:
        return np.zeros(0, dtype=int), []
    if voices:
        voices = max(1, min(int(voices), MOST_VOICES, n))
    sample = np.unique(np.linspace(0, n - 1, min(n, MOST_MERGED)).round().astype(int))
    cuts = agglomerate(unit[sample])
    if voices:
        k = min(voices, max(cuts))
    else:
        # Auto: the number of voices whose grouping is clearest - if any is
        # clear at all; one voice otherwise.
        scored = {k: silhouette(unit[sample], cuts[k]) for k in cuts if k >= 2}
        best = max(scored.values()) if scored else -1.0
        # Near-ties go to fewer voices: splitting one person in two scores
        # almost as well as the right answer, and is the worse mistake here.
        k = min((k for k, v in scored.items() if v >= best - TIE), default=1)
        if k > 1 and scored[k] < same:
            k = 1
    first = cuts[k]
    centres = _means(unit[sample], first, k)
    labels = np.argmax(unit @ centres.T, axis=1)
    centres = _means(unit, labels, k)
    labels = np.argmax(unit @ centres.T, axis=1)
    # Groups too small to be a person join the nearest real one.
    while k > 1:
        counts = np.bincount(labels, minlength=k)
        small = [c for c in range(k) if counts[c] < max(LEAST_PRINTS, LEAST_SHARE * n)]
        if not small or (voices and k <= voices and all(counts)):
            break
        drop = min(small, key=lambda c: counts[c])
        keep = [c for c in range(k) if c != drop]
        centres = centres[keep]
        k -= 1
        labels = np.argmax(unit @ centres.T, axis=1)
        centres = _means(unit, labels, k)
        labels = np.argmax(unit @ centres.T, axis=1)
    counts = np.bincount(labels, minlength=k)
    order = np.argsort(-counts, kind="stable")
    rank = np.empty(k, dtype=int)
    rank[order] = np.arange(k)
    labels = rank[labels]
    return labels, [int(c) for c in counts[order] if c]
