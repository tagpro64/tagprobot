import random
import time
import sys
from threading import Thread

from tagpro_core import TagProCore


class PlayBot(TagProCore):
    def __init__(self, group_id):
        super().__init__(name="playbot")
        self.group_id = group_id
        self.pid = None
        self.spawned = False

    def run(self):
        self.group.game.on_event(lambda e, d: e == "id", self.set_pid)
        self.group.game.on_event(lambda e, d: e == "p", self.refresh_if_dead)
        self.join_group(self.group_id)
        tick = 0
        keys = ("up", "up", "left", "right")

        while True:
            if not self.group.game.connected:
                time.sleep(0.5)
                continue

            down = random.sample(keys, random.randint(1, len(keys)))
            for key in down:
                tick += 1
                self.group.game.emit("keydown", {"k": key, "t": tick})

            time.sleep(0.5)

            for key in down:
                tick += 1
                self.group.game.emit("keyup", {"k": key, "t": tick})

    def set_pid(self, pid):
        self.pid = pid

    def refresh_if_dead(self, data):
        players = data.get("u", []) if isinstance(data, dict) else []
        player = next((p for p in players if p.get("id") == self.pid), {})
        self.spawned = self.spawned or bool(player and not player.get("dead"))
        if self.spawned and player.get("dead"):
            self.pid = None
            self.spawned = False
            self.group.game.clear_connection()
            self.group.handle_launched_game(force=True)


if __name__ == "__main__":
    num_bots = int(sys.argv[1])
    group_id = sys.argv[2]
    for _ in range(num_bots):
        Thread(target=PlayBot(group_id).run, daemon=True).start()
    while True:
        time.sleep(1)
