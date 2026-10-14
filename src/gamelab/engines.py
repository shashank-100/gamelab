"""One entry point for both game engines. A game file with a `teams:` key is a brawler (v2); otherwise an arena (v1)."""
from pathlib import Path

import yaml

from . import dsl as arena_dsl
from . import engine as arena_engine
from .brawl import dsl as brawl_dsl
from .brawl import engine as brawl_engine

DOCS = Path(__file__).resolve().parents[2] / "docs"


def kind_of(source) -> str:
    data = source
    if isinstance(source, str):
        try:
            data = yaml.safe_load(source)
        except yaml.YAMLError:
            return "arena"
    return "brawl" if isinstance(data, dict) and "teams" in data else "arena"


def validate(source):
    return (brawl_dsl if kind_of(source) == "brawl" else arena_dsl).validate(source)


def is_brawl(spec) -> bool:
    return isinstance(spec, brawl_dsl.BrawlSpec)


def run(spec, offset=0, policy=None, record_frames=False):
    """Simulate seed `offset` of a validated spec. `policy` only applies to arena games (the player's policy)."""
    if is_brawl(spec):
        return brawl_engine.simulate(brawl_engine.with_seed(spec, offset), record_frames=record_frames)
    kw = {"policy": policy} if policy else {}
    return arena_engine.simulate(arena_engine.with_seed(spec, offset), record_frames=record_frames, **kw)


def doc_for(kind: str) -> str:
    return (DOCS / ("BRAWL.md" if kind == "brawl" else "DSL.md")).read_text()
