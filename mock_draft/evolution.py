"""Co-evolutionary optimizer: teams learn to bid better against EACH OTHER
(not against a fixed target), optimizing for total roster projected points
-- the actual "max point roster" objective, not dollar efficiency.

Each generation, genomes from the population are randomly assigned to the
12 real teams (different pairing every match, so a genome's fitness
reflects how it does against a variety of opponents, not one fixed
matchup), a full mock auction is run, and each genome's fitness is its
average roster points across every match it appeared in. The population
then evolves: elites survive, the rest are bred via crossover + mutation
of the fitter genomes, and a few fresh random genomes are injected every
generation to keep exploring. Population state persists to disk
(learned_population.json) so intelligence accumulates across separate
runs of this script, not just within one.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .archetypes import Archetype
from .auction import run_single_auction
from .genome import crossover, genome_from_dict, genome_to_dict, mutate, random_genome
from .models import Player, Team

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


def evaluate_generation(
    population: list[Archetype],
    players: dict[str, Player],
    teams_template: dict[str, Team],
    matches_per_generation: int,
    rng: np.random.Generator,
) -> np.ndarray:
    """Return an array of avg fitness (roster points) per genome index.
    Every genome appears in multiple, differently-matched auctions per
    generation so its score reflects robustness, not one lucky pairing."""
    team_names = list(teams_template.keys())
    n_teams = len(team_names)
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
            fitness_sums[gi] += final_teams[team_name].total_points
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
) -> tuple[list[Archetype], list[GenerationStats]]:
    rng = np.random.default_rng(seed)

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
        fitness = evaluate_generation(population, players, teams_template, matches_per_generation, rng)
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
            print(f"  gen {gen:>3}: best={stats.best_fitness:.1f} pts  mean={stats.mean_fitness:.1f}  worst={stats.worst_fitness:.1f}")

        population = evolve_step(population, fitness, rng, gen + 1)

    save_state(state_path, population, history)
    return population, history
