import time

from tagpro_core import TagProCore


class KickBot:
    def __init__(self):
        self.group = TagProCore().join_or_create_group("🥾 Kick 🥾")
        self.group.on_event(lambda name, data: name == "member", self.kick_player)

    def kick_player(self, data):
        player_id = data.get("id")
        if player_id is not None and player_id != self.group.my_id:
            time.sleep(1)
            self.group.emit("kick", player_id)

    def run(self):
        while True:
            time.sleep(60)


if __name__ == "__main__":
    KickBot().run()
