import math
import random
import sqlite3
from collections import deque
from pathlib import Path

from tagpro_core import TagProCore, Team


"""
TODO:
Void game if player is missing at time=0.0
If voided, move afk player(s) to afk group
if player leaves group mid-game, other player auto-wins
auto-end when unwinnable

AFK command
QUEUE command (shows QUEUE position and afk)
"""


class ELOStats:
    DEFAULT = 1500, 350, 0.06

    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self.db.execute("CREATE TABLE IF NOT EXISTS elo (id TEXT PRIMARY KEY, r REAL, rd REAL, sig REAL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS elo_names (id TEXT PRIMARY KEY, name TEXT)")

    def _get(self, pid):
        self.db.execute("INSERT OR IGNORE INTO elo VALUES (?, ?, ?, ?)", (str(pid), *self.DEFAULT))
        return self.db.execute("SELECT r, rd, sig FROM elo WHERE id = ?", (str(pid),)).fetchone()

    def get_stats(self, pid):
        elo = self._get(pid)[0]
        rank = self.db.execute("SELECT 1 + COUNT(*) FROM elo WHERE r > ?", (elo,)).fetchone()[0]
        return elo, rank

    def top(self):
        return self.db.execute("SELECT COALESCE(name, id), r FROM elo LEFT JOIN elo_names USING (id) ORDER BY r DESC LIMIT 5")

    def update(self, pids, scores, names):
        old = [self._get(pid) for pid in pids]
        new = [self._glicko2(p, o, s) for p, o, s in zip(old, old[::-1], scores)]
        self.db.executemany(
            "REPLACE INTO elo VALUES (?, ?, ?, ?)",
            ((str(pid), *stat) for pid, stat in zip(pids, new)),
        )
        self.db.executemany("REPLACE INTO elo_names VALUES (?, ?)", ((str(pid), name) for pid, name in zip(pids, names)))
        return {pid: {"elo": n[0], "change": n[0] - o[0]} for pid, o, n in zip(pids, old, new)}

    def _glicko2(self, player, opponent, result, tau=0.5, eps=1e-6):
        q, t, score = 173.7178, tau * tau, (result + 1) / 2
        mu, phi = (player[0] - 1500) / q, player[1] / q
        mu2, phi2 = (opponent[0] - 1500) / q, opponent[1] / q
        g = 1 / math.sqrt(1 + 3 * phi2 * phi2 / (math.pi * math.pi))
        e = 1 / (1 + math.exp(-g * (mu - mu2)))
        v = 1 / (g * g * e * (1 - e))
        d = v * g * (score - e)
        a, p, d2 = math.log(player[2] ** 2), phi * phi, d * d

        def f(x):
            y = math.exp(x)
            z = p + v + y
            return y * (d2 - p - v - y) / (2 * z * z) - (x - a) / t

        A = a
        B = math.log(d2 - p - v) if d2 > p + v else a - tau
        while d2 <= p + v and f(B) < 0:
            B -= tau

        fA, fB = f(A), f(B)
        while abs(B - A) > eps:
            C = A + (A - B) * fA / (fB - fA)
            fC = f(C)
            A, fA = (B, fB) if fC * fB <= 0 else (A, fA / 2)
            B, fB = C, fC

        sig = math.exp(A / 2)
        phi = 1 / math.sqrt(1 / (p + sig * sig) + 1 / v)
        return 1500 + q * (mu + phi * phi * g * (score - e)), q * phi, sig


class OFMBot(TagProCore):
    def __init__(self, preset="gZMtibMaApxZTSbqryhmAWptdoyaCG", elo_db_path=".data/elo_stats.sqlite3"):
        super().__init__(name="ofm_bot")
        self.preset = preset
        self.queue = deque()
        self.game_players = {}
        self.elo_stats = ELOStats(elo_db_path)
        self.group.game.hook("clientInfo", self.game_players.clear, keys=())
        self.group.game.hook("p", self.handle_game_players)
        self.group.game.hook("end", self.handle_game_result)
        self.group.hook({"member", "team"}, self.handle_waiting_player,
                        keys="id", required="id", match={"team": Team.WAITING})
        self.group.hook("chat", self.handle_top_chat, keys=(), match={"message": "TOP"})
        self.group.register_task(self.handle_group, 2)
        self.join_or_create_group("🌾 OFM 🌾")

    def handle_waiting_player(self, player_id):
        if self.group.my_id is not None and player_id != self.group.my_id:
            self.group.set_team(Team.SPECTATORS, player_id)
            if player_id not in self.queue:
                self.queue.append(player_id)

    def handle_top_chat(self):
        self.group.send_chat("\n".join(
            f"{name} (ELO: {elo:.0f}, Rank: #{rank})"
            for rank, (name, elo) in enumerate(self.elo_stats.top(), 1)
        ) or "No ELO yet")

    def handle_game_players(self, data):
        players = data.get("u", data) if isinstance(data, dict) else data
        ready = sum(p.get("team") in (Team.RED, Team.BLUE) for p in self.game_players.values())
        for player in players if isinstance(players, list) else ():
            if isinstance(player, dict) and player.get("id") is not None:
                self.game_players.setdefault(player["id"], {}).update(player)
        playing = [p for p in self.game_players.values() if p.get("team") in (Team.RED, Team.BLUE)]
        if ready < 2 == len(playing):
            unauthed = [p.get("name", p["id"]) for p in playing if not p.get("auth")]
            if unauthed:
                self.group.send_chat(f"No ELO, {', '.join(map(str, unauthed))} not authed")
                return
            self.group.send_chat("\n".join(
                f"{p.get('name', p['id'])} (ELO: {s[0]:.0f}, Rank: #{s[1]})"
                for p, s in ((p, self.elo_stats.get_stats(p["auth"])) for p in playing)
            ))

    def handle_game_result(self, _data):
        players = [p for p in self.game_players.values() if p.get("team") in (Team.RED, Team.BLUE)]
        if len(players) != 2:
            return self.game_players.clear()
        holds = [player.get("s-hold", 0) for player in players]
        result = (holds[0] > holds[1]) - (holds[0] < holds[1])
        names = [self.group.players.get(p["sessionId"], p).get("name", f"Player {p['id']}") for p in players]
        queue = self.queue
        queue.extend(p["sessionId"] for p, score in zip(players, (result, -result)) if score <= 0 and p["sessionId"] not in queue)
        if any(not p.get("auth") for p in players):
            self.game_players.clear()
            self.group.end_game()
            return
        pids = [p["auth"] for p in players]
        ratings = self.elo_stats.update(pids, (result, -result), names)
        words = ("draw", "draw") if not result else ("wins", "loss")
        self.group.send_chat("\n".join(
            f"{names[i]} {word}, ELO: {ratings[pids[i]]['elo']:.0f} ({ratings[pids[i]]['change']:+.0f})"
            for i, word in zip((0, 1) if result >= 0 else (1, 0), words)
        ))
        self.game_players.clear()
        self.group.end_game()

    def start_next_game(self):
        active = [
            pid for pid, player in self.group.players.items()
            if player["team"] in (Team.RED, Team.BLUE) and pid != self.group.my_id
        ]
        if len(active) == 2 and self.queue and set(self.queue).isdisjoint(active):
            self.queue.append(random.choice(active))

        players = [pid for pid in active if pid not in self.queue]
        players += [pid for pid in self.queue if pid in self.group.players and pid not in players]
        players = players[:2]
        if len(players) < 2:
            return

        self.group.set_preset(self.preset)
        for player_id in set(active) - set(players):
            self.group.set_team(Team.SPECTATORS, player_id)
        self.group.set_team(Team.RED, players[0])
        self.group.set_team(Team.BLUE, players[1])

        if set(active) != set(players):
            return

        self.queue = deque(pid for pid in self.queue if pid not in players)
        self.group.launch_game()

    def handle_group(self):
        self.group.set_team(Team.SPECTATORS)
        if not self.group.game_active:
            self.start_next_game()


if __name__ == "__main__":
    OFMBot().daemon()
