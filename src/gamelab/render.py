"""pygame front end: play with keyboard (WASD move, 1-4 abilities, mouse aims) or watch the auto player."""
import os

from .engine import Command, Sim
from .policies import auto_policy

SCALE = 20
COLORS = {"player": (80, 170, 255), "ally": (120, 220, 160), "enemy": (235, 90, 80)}


def _draw(pg, screen, font, sim: Sim, msg=""):
    screen.fill((22, 24, 30))
    W, H = sim.g.arena.width, sim.g.arena.height
    pg.draw.rect(screen, (60, 64, 76), (0, 0, W * SCALE, H * SCALE), 2)
    for s in sim.shots:
        pg.draw.circle(screen, (255, 200, 60), (s.x * SCALE, s.y * SCALE), max(3, s.radius * SCALE))
    for u in sim.units:
        if not u.alive:
            continue
        c = (u.x * SCALE, u.y * SCALE)
        pg.draw.circle(screen, COLORS[u.team], c, u.radius * SCALE)
        w = 40
        pg.draw.rect(screen, (70, 20, 20), (c[0] - w / 2, c[1] - u.radius * SCALE - 10, w, 5))
        pg.draw.rect(screen, (90, 220, 90), (c[0] - w / 2, c[1] - u.radius * SCALE - 10, w * u.hp / u.max_hp, 5))
        screen.blit(font.render(f"{u.name} {u.hp:.0f}", True, (220, 220, 220)), (c[0] - w / 2, c[1] + u.radius * SCALE + 2))
    player = next(u for u in sim.units if u.team == "player")
    hud = "  ".join(f"[{i + 1}] {a.name}{'' if sim.ready(player, a.name) else ' …'}" for i, a in enumerate(player.spec.abilities))
    screen.blit(font.render(f"{sim.g.name}  t={sim.t:5.1f}s   {hud}   {msg}", True, (230, 230, 230)), (8, H * SCALE + 6))


def run(spec, mode="play", screenshot_dir=None, every=1.0):
    if screenshot_dir:
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    import pygame as pg

    pg.init()
    screen = pg.display.set_mode((int(spec.game.arena.width * SCALE), int(spec.game.arena.height * SCALE) + 30))
    pg.display.set_caption(f"GameLab — {spec.game.name}")
    font = pg.font.SysFont("menlo", 13)
    clock = pg.time.Clock()
    pending = {"use": None}

    def keyboard(obs):
        k = pg.key.get_pressed()
        mv = ((k[pg.K_d] or k[pg.K_RIGHT]) - (k[pg.K_a] or k[pg.K_LEFT]), (k[pg.K_s] or k[pg.K_DOWN]) - (k[pg.K_w] or k[pg.K_UP]))
        mx, my = pg.mouse.get_pos()
        use, pending["use"] = pending["use"], None
        return Command(move=mv, use=use, aim=(mx / SCALE, my / SCALE) if pg.mouse.get_focused() else None)

    sim = Sim(spec, policy=keyboard if mode == "play" else auto_policy)
    names = [a.name for a in spec.characters[0].abilities]
    outcome, shots_taken, next_shot = None, 0, 0.0
    while True:
        for ev in pg.event.get():
            if ev.type == pg.QUIT or (ev.type == pg.KEYDOWN and ev.key == pg.K_ESCAPE):
                pg.quit()
                return outcome
            if ev.type == pg.KEYDOWN and pg.K_1 <= ev.key <= pg.K_9 and ev.key - pg.K_1 < len(names):
                pending["use"] = names[ev.key - pg.K_1]
        if outcome is None:
            outcome = sim.step()
        _draw(pg, screen, font, sim, f"[{outcome.upper()}] Esc to quit" if outcome else "")
        pg.display.flip()
        if screenshot_dir and (sim.t >= next_shot or outcome):
            os.makedirs(screenshot_dir, exist_ok=True)
            pg.image.save(screen, os.path.join(screenshot_dir, f"frame_{shots_taken:03d}.png"))
            shots_taken += 1
            next_shot += every
            if outcome:
                pg.quit()
                return outcome
        if not screenshot_dir:
            clock.tick(spec.game.tick_rate)
