"""Lane registry. A lane is one public source: fetch() returns today's entities, signals() reads the diff."""
from __future__ import annotations

from tideline.lanes import mcp, models, packages, reliability, teams

LANES = [models.LANE, mcp.LANE, packages.LANE, reliability.LANE, teams.LANE]
