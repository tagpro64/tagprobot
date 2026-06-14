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
        self.keys_down = []

        self.group.game.hook("id", lambda pid: setattr(self, "pid", pid))
        self.group.game.hook("p", self.refresh_if_dead)
        self.group.game.register_task(self.move, 0.5)
        self.join_group(self.group_id)

    def move(self):
        keys_down = random.sample(("up", "up", "left", "right"), random.randint(1, 4))
        self.keys_down.extend(keys_down)
        for key in keys_down:
            self.group.game.key_down(key)

        keys_up = random.sample(self.keys_down, random.randint(1, len(self.keys_down)))
        for key in keys_up:
            self.group.game.key_up(key)
            self.keys_down.remove(key)

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
        Thread(target=PlayBot(group_id).daemon, daemon=True).start()
    while True:
        time.sleep(1)
