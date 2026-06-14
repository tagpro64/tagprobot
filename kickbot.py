import time

from tagpro_core import TagProCore


class KickBot(TagProCore):
    def __init__(self):
        super().__init__(name="kickbot")
        self.group.hook(lambda name, data: name == "member", self.kick_player)
        self.join_or_create_group("🥾 Kick 🥾")

    def kick_player(self, data):
        player_id = data.get("id")
        if player_id is not None and player_id != self.group.my_id:
            time.sleep(2)
            self.group.send_chat("wtf, who are you")
            time.sleep(3)
            self.group.emit("kick", player_id)


if __name__ == "__main__":
    KickBot().daemon()
