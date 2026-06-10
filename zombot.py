import random
import time

from tagpro_core import TagProCore, Team


class ZombiesBot:
    def __init__(self, presets):
        self.presets = presets
        self.group = TagProCore().join_or_create_group("🧟 Zombies 🧟")
        self.group.game.on_game_players_update(self.update_game_player)
        self.group.game.on_event(lambda name, data: name == "tag", self.handle_tag)
        self.group.game.on_event(
            lambda name, data: name == "time" and isinstance(data, dict) and data.get("state") == 1,
            self.force_everyone_blue
        )
        self.group.on_event(
            lambda name, data: (name in {"member", "team"} and data.get("team") == Team.WAITING),
            self.handle_waiting_player,
        )

    def handle_waiting_player(self, data):
        if self.group.game_active:
            self.group.set_team(Team.BLUE, data["id"])
            self.group.launch_game()

    @property
    def humans(self):
        return set(self.group.players) - {self.group.my_id}

    def update_game_player(self, players):
        if not self.group.game.started:
            return
        red_pops = [
            player.get("s-pops", 0) for player in players.values()
            if player.get("team") == Team.RED
        ]
        if (not red_pops or min(red_pops) >= 1) and self.group.end_game(delay=3):
            self.group.send_chat("Zomballs win")

    def handle_tag(self, players):
        blue, red = players
        action = random.choice(["zombified", "CHOMPED", "zombstepped", "slaughtered", "devoured", "consumed"])
        self.group.send_chat(f"[¬º-°]¬ *{blue['name']}* {action} *{red['name']}*")

    def force_everyone_blue(self, data):
        # force all players blue so refresh makes you zombie
        for player_id in self.humans:
            self.group.set_team(Team.BLUE, player_id)
        self.group.send_chat(
            "Welcome to zomball. If a zombie tags you, refresh to "
            "automatically turn zombie."
        )

    def apply_base_settings(self):
        self.group.set_settings(
            selfAssignment="false",
            blueTeamName="Zomballs",
            redTeamName="Huballs",
        )
        self.group.set_team(Team.SPECTATORS, self.group.my_id)

    def setup_and_launch_game(self):
        # prepare map, and move all to red except 1 then launch
        time.sleep(5)
        self.group.set_preset(random.choice(self.presets))
        zombie_id = random.choice(list(self.humans))
        self.group.set_team(Team.BLUE, zombie_id)
        for player_id in set(self.humans) - {zombie_id}:
            self.group.set_team(Team.RED, player_id)
        time.sleep(5)
        self.group.launch_game()
        time.sleep(3)

    def run(self):
        while True:
            time.sleep(2)
            self.apply_base_settings()
            if not self.group.game_active:
                self.group.end_game()
                if len(self.humans) >= 2:
                    self.setup_and_launch_game()


if __name__ == "__main__":
    bot = ZombiesBot(presets=[
        "gZMefKogggtdoiaaaJyaraaksaaamabsuaaaVadSUadSZnqqdHtzYjbE",
        # "gZMefKnNggteoiaaaJyaraaksaaamaaEuaaaVadSUadSZnqqdHtzYjbEF",
        "gZMefKnRggtdoiaaaJyaraaksaaamabsuaaaVadSUadSZnqqdHtzYjbE",
        "gZMefKoLggtdoiaaaJyaraaksaaamabsuaaaVadSUadSZnqqdHtzYjbE"
        "gZMefKphggtdoiaaaJyaraaksaaamabsuaaaVnqqUnqqZadSdHtzYTE",
    ])
    bot.run()
