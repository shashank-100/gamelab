"""pygame front end for the brawler.

play:  you control the first red fighter. WASD/arrows move, J = button a, K = button b, J+K together = a+b.
       Hold a channel button to build power, let go to release it. Esc quits.
watch: bots play every fighter.
"""
import math
import os

from .engine import Command, Sim
from .bots import bot

SCALE = 22
TEAM_COLORS = [(235, 95, 85), (80, 165, 255), (120, 220, 150), (240, 200, 90)]
COMBO_WINDOW = 0.08  # seconds to press the second button for a+b


def run(spec, mode="play", screenshot_dir=None, every=1.0):
    if screenshot_dir:
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    import pygame as pg

    pg.init()
    R = spec.game.platform.radius
    size = int((R + 3) * 2 * SCALE)
    screen = pg.display.set_mode((size, size + 34))
    pg.display.set_caption(f"GameLab brawler — {spec.game.name}")
    font = pg.font.SysFont("menlo", 13)
    clock = pg.time.Clock()
    to_px = lambda p: (size / 2 + p[0] * SCALE, size / 2 + p[1] * SCALE)

    teams = list(spec.teams)
    me_team = teams[0]
    state = {"pending": None, "pending_t": 0.0, "held": set()}
    sim_ref = {}

    def human(obs):
        sim = sim_ref["sim"]
        k = pg.key.get_pressed()
        mv = ((k[pg.K_d] or k[pg.K_RIGHT]) - (k[pg.K_a] or k[pg.K_LEFT]), (k[pg.K_s] or k[pg.K_DOWN]) - (k[pg.K_w] or k[pg.K_UP]))
        held = {b for b, key in (("a", pg.K_j), ("b", pg.K_k)) if k[key]}
        press = None
        if state["pending"] and (sim.t - state["pending_t"] >= COMBO_WINDOW or held >= {"a", "b"}):
            btn = "a+b" if held >= {"a", "b"} else state["pending"]
            press = next((n for n, a in obs["abilities"].items() if a["button"] == btn), None)
            state["pending"] = None
            state["channel_btns"] = {"a", "b"} if btn == "a+b" else {btn}
        release = bool(obs["self"]["channel"]) and not (held & state.get("channel_btns", set()))
        return Command(move=mv, press=press, release=release)

    def human_team(obs):
        return human(obs) if obs["self"]["name"] == my_name else bot(obs)

    policies = {me_team: human_team} if mode == "play" else {}
    sim = Sim(spec, policies=policies, record_frames=False)
    sim_ref["sim"] = sim
    my_name = next(u.name for u in sim.units if u.team == me_team)
    outcome, shot_n, next_shot = None, 0, 0.0
    while True:
        for ev in pg.event.get():
            if ev.type == pg.QUIT or (ev.type == pg.KEYDOWN and ev.key == pg.K_ESCAPE):
                pg.quit()
                return outcome
            if ev.type == pg.KEYDOWN and ev.key in (pg.K_j, pg.K_k) and not state["pending"]:
                state["pending"], state["pending_t"] = ("a" if ev.key == pg.K_j else "b"), sim.t
        if outcome is None:
            outcome = sim.step()
        # draw
        screen.fill((18, 20, 26))
        pg.draw.circle(screen, (58, 62, 74), to_px((0, 0)), R * SCALE)
        pg.draw.circle(screen, (95, 100, 118), to_px((0, 0)), R * SCALE, 2)
        for w in sim.walls:
            pg.draw.line(screen, (170, 140, 100), to_px(w["a"]), to_px(w["b"]), 6)
        for r in sim.rocks:
            pg.draw.circle(screen, (150, 120, 90), to_px((r["x"], r["y"])), r["ab"].rock_radius * SCALE)
        for s in sim.shots:
            pg.draw.circle(screen, (255, 210, 80), to_px((s["x"], s["y"])), max(3, s["ab"].radius * SCALE))
        for u in sim.units:
            if not u.alive:
                continue
            col = TEAM_COLORS[teams.index(u.team) % len(TEAM_COLORS)]
            c = to_px(u.pos)
            rad = u.spec.radius * SCALE * (1.25 if sim.airborne(u) else 1.0)
            if sim.airborne(u):
                pg.draw.circle(screen, (10, 10, 12), (c[0], c[1] + 6), u.spec.radius * SCALE)
            pg.draw.circle(screen, col, c, rad)
            if u.channel:
                held = sim.t - u.channel["start"]
                frac = min(1.0, held / u.channel["ab"].max_time)
                pg.draw.circle(screen, (255, 255, 255), c, rad + 4 + 10 * frac, 2)
            if u.name == my_name and mode == "play":
                pg.draw.circle(screen, (255, 255, 255), c, rad + 2, 1)
            screen.blit(font.render(f"{u.dmg:.0f}%", True, (235, 235, 235)), (c[0] - 12, c[1] - rad - 16))
        hud = f"{spec.game.name}  t={sim.t:5.1f}s"
        if mode == "play":
            me = next(u for u in sim.units if u.name == my_name)
            hud += "   " + "  ".join(f"[{a.button.upper().replace('A','J').replace('B','K')}] {a.name}{'' if sim.ready(me, a) else ' …'}" for a in me.spec.abilities)
        if outcome:
            hud += f"   [{outcome.upper()} WINS]" if outcome != "draw" else "   [DRAW]"
        screen.blit(font.render(hud, True, (230, 230, 230)), (8, size + 8))
        pg.display.flip()
        if screenshot_dir and (sim.t >= next_shot or outcome):
            os.makedirs(screenshot_dir, exist_ok=True)
            pg.image.save(screen, os.path.join(screenshot_dir, f"frame_{shot_n:03d}.png"))
            shot_n += 1
            next_shot += every
            if outcome:
                pg.quit()
                return outcome
        if not screenshot_dir:
            clock.tick(spec.game.tick_rate)
