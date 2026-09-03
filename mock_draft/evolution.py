"""Co-evolutionary optimizer: teams learn to bid better against EACH OTHER
(not against a fixed target), optimizing for legal-lineup roster utility
-- the actual "max legally-startable-points roster" objective, not dollar
efficiency, and NOT the naive sum of all 15 rostered players' points.

PHASE 2 CHANGE: fitness is now
legal_lineup.build_production_lineup(roster).total_roster_utility (legal
starting lineup points + weighted bench option value) instead of
Team.total_points (which summed every rostered player equally and was
the root cause of the retracted "overweight QB" result -- see
outputs/auction_rebuild/audit/current_architecture.md and STRATEGY.md).
An illegal final roster contributes 0 utility for that match rather than
being silently scored on its (illegal) point total.

Each generation, genomes from the population are randomly assigned to the
12 real teams (different pairing every match, so a genome's fitness
reflects how it does against a variety of opponents, not one fixed
matchup), a full mock auction is run, and each genome's fitness is its
average legal-lineup utility across every match it appeared in. The
population then evolves: elites survive, the rest are bred via crossover
+ mutation of the fitter genomes, and a few fresh random genomes are
injected every generation to keep exploring. Population state persists to
disk (learned_population.json) so intelligence accumulates across
separate runs of this script, not just within one.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .archetypes import Archetype
from .auction import run_single_auction
from .genome import crossover, genome_from_dict, genome_to_dict, mutate, random_genome
from .legal_lineup import build_production_lineup
from .models import Player, Team


def _roster_utility(team: Team) -> float:
    """Legal-lineup utility for one finished team -- 0 if the roster can't
    field a legal starting lineup (never silently fall back to raw
    points for an illegal roster)."""
    return build_production_lineup(team.roster).total_roster_utility

DEFAULT_STATE_PATH = Path(__file__).parent.parent / "mock_draft_learned_population.json"

ELITE_FRAC = 0.25
RANDOM_INJECT_FRAC = 0.10


@dataclass
class GenerationStats:
    generation: int
    best_fitness: float
    mean_fitness: float
    worst_fitness: float
    best_genome_name: str


def init_population(size: int, rng: np.random.Generator, start_gen: int = 0) -> list[Archetype]:
    return [random_genome(rng, name=f"gen{start_gen}_seed{i}") for i in range(size)]


def compute_team_baselines(
    players: dict[str, Player], teams_template: dict[str, Team], n_matches: int, rng: np.random.Generator
) -> dict[str, float]:
    """Each real team's typical roster points, averaged over many
    hand-designed-archetype drafts, regardless of strategy. This exists
    because team identity (i.e. whose keepers you inherit) turned out to
    have a ~160-point fixed-effect spread across the 12 teams -- bigger
    than the entire range the evolved population's fitness has moved
    across 22 generations. Without subtracting this out, a genome's score
    is dominated by which team-slot it randomly landed on, not by whether
    its bidding strategy is actually any good."""
    totals = {name: [] for name in teams_template}
    for _ in range(n_matches):
        _, final_teams = run_single_auction(players, teams_template, rng)
        for name, team in final_teams.items():
            totals[name].append(_roster_utility(team))
    return {name: float(np.mean(vals)) for name, vals in totals.items()}


def evaluate_generation(
    population: list[Archetype],
    players: dict[str, Player],
    teams_template: dict[str, Team],
    matches_per_generation: int,
    rng: np.random.Generator,
    team_baselines: dict[str, float] | None = None,
) -> np.ndarray:
    """Return an array of avg fitness per genome index. Fitness is roster
    points MINUS that team-slot's baseline (see compute_team_baselines) --
    without this adjustment, keeper-driven team differences swamp any
    real strategy signal. Every genome appears in multiple, differently-
    matched auctions per generation so its score reflects robustness, not
    one lucky pairing."""
    team_names = list(teams_template.keys())
    n_teams = len(team_names)
    baselines = team_baselines or {name: 0.0 for name in team_names}
    fitness_sums = np.zeros(len(population))
    fitness_counts = np.zeros(len(population))

    for _ in range(matches_per_generation):
        replace = len(population) < n_teams
        genome_idx = rng.choice(len(population), size=n_teams, replace=replace)
        rng.shuffle(genome_idx)
        strategies = {team_names[i]: population[genome_idx[i]] for i in range(n_teams)}

        _, final_teams = run_single_auction(players, teams_template, rng, strategies=strategies)

        for i, team_name in enumerate(team_names):
            gi = genome_idx[i]
            adjusted_points = _roster_utility(final_teams[team_name]) - baselines[team_name]
            fitness_sums[gi] += adjusted_points
            fitness_counts[gi] += 1

    # Genomes never sampled this generation (possible with a large
    # population and few matches) keep no signal -- treat as unknown
    # rather than penalizing; evolve_step re-samples them next generation.
    with np.errstate(invalid="ignore", divide="ignore"):
        avg = np.where(fitness_counts > 0, fitness_sums / np.maximum(fitness_counts, 1), np.nan)
    return avg


def evolve_step(
    population: list[Archetype], fitness: np.ndarray, rng: np.random.Generator, generation: int
) -> list[Archetype]:
    valid = ~np.isnan(fitness)
    ranked_idx = np.argsort(np.where(valid, fitness, -np.inf))[::-1]

    n = len(population)
    n_elite = max(1, int(round(n * ELITE_FRAC)))
    n_random = max(1, int(round(n * RANDOM_INJECT_FRAC)))
    n_bred = n - n_elite - n_random

    elites = [population[i] for i in ranked_idx[:n_elite]]
    elite_pool = elites if elites else [population[i] for i in ranked_idx[:1]]

    next_gen = []
    for i, e in enumerate(elites):
        next_gen.append(Archetype(**{**genome_to_dict(e), "name": f"gen{generation}_elite{i}"}))

    for i in range(n_bred):
        a, b = rng.choice(len(elite_pool), size=2, replace=len(elite_pool) < 2)
        child = crossover(elite_pool[a], elite_pool[b], rng, name=f"gen{generation}_bred{i}")
        child = mutate(child, rng, name=f"gen{generation}_bred{i}")
        next_gen.append(child)

    for i in range(n_random):
        next_gen.append(random_genome(rng, name=f"gen{generation}_random{i}"))

    return next_gen[:n]


def save_state(path: Path, population: list[Archetype], history: list[GenerationStats]) -> None:
    data = {
        "population": [genome_to_dict(g) for g in population],
        "history": [vars(h) for h in history],
    }
    path.write_text(json.dumps(data, indent=2))


def load_state(path: Path) -> tuple[list[Archetype] | None, list[GenerationStats]]:
    if not path.exists():
        return None, []
    data = json.loads(path.read_text())
    population = [genome_from_dict(g) for g in data["population"]]
    history = [GenerationStats(**h) for h in data["history"]]
    return population, history


def run_evolution(
    generations: int,
    population_size: int,
    matches_per_generation: int,
    players: dict[str, Player],
    teams_template: dict[str, Team],
    seed: int = 0,
    state_path: Path = DEFAULT_STATE_PATH,
    verbose: bool = True,
    baseline_matches: int = 40,
) -> tuple[list[Archetype], list[GenerationStats]]:
    rng = np.random.default_rng(seed)

    if verbose:
        print(f"Computing per-team point baselines ({baseline_matches} hand-designed-archetype drafts)"
              " so genome fitness isn't dominated by keeper luck...")
    team_baselines = compute_team_baselines(players, teams_template, baseline_matches, rng)
    if verbose:
        spread = max(team_baselines.values()) - min(team_baselines.values())
        print(f"  Team baseline spread: {spread:.0f} pts "
              f"(min={min(team_baselines.values()):.0f}, max={max(team_baselines.values()):.0f})")

    population, history = load_state(state_path)
    start_gen = history[-1].generation + 1 if history else 0
    if population is None:
        population = init_population(population_size, rng, start_gen=start_gen)
        if verbose:
            print(f"No saved population at {state_path} -- starting fresh with {population_size} random genomes.")
    else:
        if verbose:
            print(f"Resuming from {state_path}: generation {start_gen}, population size {len(population)}.")
        if len(population) != population_size:
            # Resize by cloning/truncating -- keeps learned genomes rather
            # than discarding them just because the population size changed.
            if len(population) < population_size:
                extra = init_population(population_size - len(population), rng, start_gen=start_gen)
                population = population + extra
            else:
                population = population[:population_size]

    for gen in range(start_gen, start_gen + generations):
        fitness = evaluate_generation(population, players, teams_template, matches_per_generation, rng, team_baselines)
        valid_fitness = fitness[~np.isnan(fitness)]
        best_i = int(np.nanargmax(fitness))
        stats = GenerationStats(
            generation=gen,
            best_fitness=float(np.nanmax(fitness)),
            mean_fitness=float(np.nanmean(valid_fitness)) if len(valid_fitness) else float("nan"),
            worst_fitness=float(np.nanmin(valid_fitness)) if len(valid_fitness) else float("nan"),
            best_genome_name=population[best_i].name,
        )
        history.append(stats)
        if verbose:
            print(f"  gen {gen:>3}: best={stats.best_fitness:+.1f}  mean={stats.mean_fitness:+.1f}  worst={stats.worst_fitness:+.1f}"
                  f"  (pts above/below that team's baseline)")

        population = evolve_step(population, fitness, rng, gen + 1)

    save_state(state_path, population, history)
    return population, history
