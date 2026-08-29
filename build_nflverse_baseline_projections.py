import sys
sys.path.insert(0, ".")
from auction_model import nflverse_pull
import pandas as pd

stats = pd.read_csv("data/nflverse/player_stats_reg_2025.csv")
proj = nflverse_pull.build_projections_from_stats(stats, 2025)
proj.to_csv("data/projections_2026_nflverse_baseline.csv", index=False)
print(len(proj), "players")
print(proj.head(8)[["player", "position", "nfl_team", "projected_points"]])
