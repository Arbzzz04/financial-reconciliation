"""Accuracy scoring of reconciliation output against known ground truth.

Because the data generator keeps the answer key, the engine's accuracy can be
measured rather than eyeballed. Matching is scored as a set comparison of
``(a_record_id, b_record_id)`` pairs (docs/contracts.md, contract 3):

* **true positives** -- predicted pairs that are in the ground truth,
* **false positives** -- predicted pairs that are not,
* **false negatives** -- true pairs the engine missed,

from which precision, recall and F1 follow.

One subtlety: a duplicate is an exact copy of another record, so the engine
cannot (and need not) know which copy is "the original". Every record ID is
mapped to its original before scoring, and classification is compared per
duplicate group rather than per record.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

import pandas as pd

# How the engine's match passes map onto the ground truth's match types.
PASS_TO_MATCH_TYPE = {"EXACT": "ONE_TO_ONE", "TOLERANT": "ONE_TO_ONE", "BATCH": "BATCH"}


@dataclass(frozen=True)
class Score:
    """Confusion counts for one comparison, with the derived rates.

    Attributes:
        true_positives: Predictions that are correct.
        false_positives: Predictions that are wrong.
        false_negatives: Correct answers that were not predicted.
    """

    true_positives: int
    false_positives: int
    false_negatives: int

    @property
    def precision(self) -> float:
        """Share of predictions that are correct (1.0 if nothing was predicted)."""
        predicted = self.true_positives + self.false_positives
        return self.true_positives / predicted if predicted else 1.0

    @property
    def recall(self) -> float:
        """Share of correct answers that were found (1.0 if there were none)."""
        actual = self.true_positives + self.false_negatives
        return self.true_positives / actual if actual else 1.0

    @property
    def f1(self) -> float:
        """Harmonic mean of precision and recall."""
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if p + r else 0.0

    def as_dict(self) -> dict[str, float | int]:
        """Return the counts and rates as a plain dictionary."""
        return {
            "true_positives": self.true_positives,
            "false_positives": self.false_positives,
            "false_negatives": self.false_negatives,
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1": round(self.f1, 4),
        }


def score_sets(predicted: Iterable[object], actual: Iterable[object]) -> Score:
    """Score a set of predictions against a set of correct answers.

    Args:
        predicted: The engine's predictions (any hashable items).
        actual: The ground truth.

    Returns:
        The confusion counts.
    """
    predicted, actual = set(predicted), set(actual)
    return Score(
        true_positives=len(predicted & actual),
        false_positives=len(predicted - actual),
        false_negatives=len(actual - predicted),
    )


def canonicaliser(record_labels: pd.DataFrame) -> Callable[[str], str]:
    """Build a function that maps any duplicate copy to its original's ID.

    Args:
        record_labels: The generator's ``record_labels`` table.

    Returns:
        A function from record ID to canonical record ID.
    """
    copies = record_labels.dropna(subset=["duplicate_of"])
    mapping = dict(zip(copies["record_id"], copies["duplicate_of"], strict=True))
    return lambda record_id: mapping.get(record_id, record_id)


def score_matches(
    predicted: pd.DataFrame,
    ground_truth_pairs: pd.DataFrame,
    record_labels: pd.DataFrame,
) -> dict[str, Score]:
    """Score predicted matches overall and per match type.

    Args:
        predicted: The engine's ``matches`` table (``a_record_id``,
            ``b_record_id``, ``match_pass``).
        ground_truth_pairs: The generator's answer key.
        record_labels: The generator's labels (used to canonicalise duplicates).

    Returns:
        Scores keyed by ``"overall"``, ``"ONE_TO_ONE"`` and ``"BATCH"``.
    """
    canon = canonicaliser(record_labels)
    predicted_pairs = {
        (canon(a), canon(b), PASS_TO_MATCH_TYPE[kind])
        for a, b, kind in zip(
            predicted["a_record_id"],
            predicted["b_record_id"],
            predicted["match_pass"],
            strict=True,
        )
    }
    true_pairs = set(
        zip(
            ground_truth_pairs["a_record_id"],
            ground_truth_pairs["b_record_id"],
            ground_truth_pairs["match_type"],
            strict=True,
        )
    )
    scores = {
        "overall": score_sets(
            {(a, b) for a, b, _ in predicted_pairs}, {(a, b) for a, b, _ in true_pairs}
        )
    }
    for match_type in ("ONE_TO_ONE", "BATCH"):
        scores[match_type] = score_sets(
            {p for p in predicted_pairs if p[2] == match_type},
            {p for p in true_pairs if p[2] == match_type},
        )
    return scores


def score_duplicates(
    predicted_duplicates: pd.DataFrame, record_labels: pd.DataFrame
) -> Score:
    """Score duplicate detection by the original each flagged copy points to.

    Args:
        predicted_duplicates: The engine's ``duplicates`` table.
        record_labels: The generator's labels.

    Returns:
        The confusion counts.
    """
    canon = canonicaliser(record_labels)
    truth = record_labels[record_labels["status"] == "DUPLICATE"]
    return score_sets(
        {canon(d) for d in predicted_duplicates["duplicate_of"]},
        set(truth["duplicate_of"]),
    )


def classification_accuracy(
    results: pd.DataFrame, record_labels: pd.DataFrame
) -> dict[str, float]:
    """Share of records whose predicted status and category match the truth.

    Records are compared per *canonical group*: a lone record is its own group,
    and a duplicate pair forms one group whose labels are compared as a multiset,
    because the engine cannot tell which identical copy is the original.

    Args:
        results: The engine's ``reconciliation_results`` table.
        record_labels: The generator's labels.

    Returns:
        ``status_accuracy`` and ``category_accuracy``, each in ``[0, 1]``.
    """
    canon = canonicaliser(record_labels)

    def grouped(frame: pd.DataFrame, column: str) -> pd.Series:
        keyed = frame.assign(group=frame["record_id"].map(canon))
        return keyed.groupby("group")[column].agg(lambda s: tuple(sorted(s)))

    accuracy: dict[str, float] = {}
    for column in ("status", "category"):
        predicted = grouped(results, column)
        actual = grouped(record_labels, column)
        aligned = actual.index.intersection(predicted.index)
        sizes = actual.loc[aligned].map(len)
        correct = (predicted.loc[aligned] == actual.loc[aligned]) * sizes
        # float() so callers get a plain number, not a NumPy scalar
        accuracy[f"{column}_accuracy"] = float(round(correct.sum() / sizes.sum(), 4))
    return accuracy
