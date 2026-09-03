"""Evolvable bidding strategies.

An evolved genome is just an Archetype (see archetypes.py) with continuous
values instead of hand-picked ones -- the hand-designed archetypes and
evolved genomes share the exact same bidding engine (compute_willingness),
so evolution is discovering points in the same strategy space a human
described, not a separate system.
"""

from __future__ import annotations

import dataclasses

import numpy as np

from .archetypes import Archetype

POSITIONS = ("QB", "RB", "WR", "TE")

# (min, max) bounds for every mutable field -- mutation/crossover always
# clip back into these, so evolution can't wander into nonsensical params
# (e.g. negative budget fractions).
BOUNDS = {
    "max_stars": (0, 5),
    "star_ceiling_pct": (0.2, 0.95),
    "price_ceiling_pct": (0.05, 0.40),
    "tier_aggression": (1.0, 1.8),
    "noise_std": (0.05, 0.30),
    "jump_bid_prob": (0.0, 0.30),
    "tilt_after_losses": (1, 5),
    "tilt_boost": (1.0, 1.5),
}
POSITION_WEIGHT_BOUNDS = (0.5, 1.6)

# Probability a boolean gene (strict_value_ceiling) flips during mutation.
BOOL_FLIP_PROB = 0.05


def random_genome(rng: np.random.Generator, name: str) -> Archetype:
    return Archetype(
        name=name,
        max_stars=int(rng.integers(BOUNDS["max_stars"][0], BOUNDS["max_stars"][1] + 1)),
        star_ceiling_pct=float(rng.uniform(*BOUNDS["star_ceiling_pct"])),
        price_ceiling_pct=float(rng.uniform(*BOUNDS["price_ceiling_pct"])),
        tier_aggression=float(rng.uniform(*BOUNDS["tier_aggression"])),
        strict_value_ceiling=bool(rng.random() < 0.15),
        noise_std=float(rng.uniform(*BOUNDS["noise_std"])),
        jump_bid_prob=float(rng.uniform(*BOUNDS["jump_bid_prob"])),
        tilt_after_losses=int(rng.integers(BOUNDS["tilt_after_losses"][0], BOUNDS["tilt_after_losses"][1] + 1)),
        tilt_boost=float(rng.uniform(*BOUNDS["tilt_boost"])),
        position_weight={p: float(rng.uniform(*POSITION_WEIGHT_BOUNDS)) for p in POSITIONS},
    )


def _clip(value, lo, hi):
    return max(lo, min(hi, value))


def mutate(genome: Archetype, rng: np.random.Generator, name: str, sigma: float = 0.15) -> Archetype:
    """Gaussian perturbation of every continuous gene (relative to its
    bound range, so a field with a wide range mutates by more in absolute
    terms than a narrow one), integer rounding for discrete genes, and a
    small flip chance for the boolean gene."""
    changes = {}
    for field_name, (lo, hi) in BOUNDS.items():
        current = getattr(genome, field_name)
        span = hi - lo
        new_val = current + rng.normal(0, sigma * span)
        new_val = _clip(new_val, lo, hi)
        if isinstance(current, int):
            new_val = int(round(new_val))
        changes[field_name] = new_val

    new_weights = {}
    for p in POSITIONS:
        current = genome.position_weight.get(p, 1.0)
        span = POSITION_WEIGHT_BOUNDS[1] - POSITION_WEIGHT_BOUNDS[0]
        new_weights[p] = _clip(current + rng.normal(0, sigma * span), *POSITION_WEIGHT_BOUNDS)
    changes["position_weight"] = new_weights

    if rng.random() < BOOL_FLIP_PROB:
        changes["strict_value_ceiling"] = not genome.strict_value_ceiling

    changes["name"] = name
    return dataclasses.replace(genome, **changes)


def crossover(parent_a: Archetype, parent_b: Archetype, rng: np.random.Generator, name: str) -> Archetype:
    """Uniform crossover: each gene independently comes from one parent or
    the other (position_weight genes crossed per-position)."""
    changes = {}
    for field_name in BOUNDS:
        changes[field_name] = getattr(parent_a if rng.random() < 0.5 else parent_b, field_name)
    changes["strict_value_ceiling"] = (
        parent_a.strict_value_ceiling if rng.random() < 0.5 else parent_b.strict_value_ceiling
    )
    changes["position_weight"] = {
        p: (parent_a.position_weight.get(p, 1.0) if rng.random() < 0.5 else parent_b.position_weight.get(p, 1.0))
        for p in POSITIONS
    }
    changes["name"] = name
    return dataclasses.replace(parent_a, **changes)


def genome_to_dict(genome: Archetype) -> dict:
    return dataclasses.asdict(genome)


def genome_from_dict(data: dict) -> Archetype:
    return Archetype(**data)
