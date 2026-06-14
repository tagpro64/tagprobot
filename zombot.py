import random
import time

from tagpro_core import TagProCore, Team


class ZombiesBot(TagProCore):
    def __init__(self, presets_path):
        super().__init__(name="zombot")
        self.presets_path = presets_path
        self.first_tagged_id = None
        self.group.game.hook("gamePlayerUpdate", self.update_game_player)
        self.group.game.hook(lambda name, data: name == "tag", self.handle_tag)
        self.group.game.hook(
            lambda name, data: name == "time" and isinstance(data, dict) and data.get("state") == 1,
            self.force_everyone_blue
        )
        self.group.hook(
            lambda name, data: (name in {"member", "team"} and data.get("team") == Team.WAITING),
            self.handle_waiting_player,
        )
        self.group.register_task(self.handle_group, 2)

        self.join_or_create_group("🧟 Zomball 🧟")

    def handle_waiting_player(self, data):
        if self.group.loaded and self.group.game_active:
            self.group.set_team(Team.BLUE, data["id"])
            self.group.launch_game()

    @property
    def spectators(self):
        specballs = {
            pid for pid, player in self.group.players.items()
            if player.get("name", "").lower().startswith("spec")
        }
        return specballs | {self.group.my_id}

    @property
    def humans(self):
        return set(self.group.players) - self.spectators

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
        if self.first_tagged_id is None:
            self.first_tagged_id = red.get("sessionId") or ""
        action = random.choice(["zombified", "CHOMPED", "zombstepped", "slaughtered", "devoured", "consumed"])
        self.group.send_chat(f"[¬º-°]¬ *{blue['name']}* {action} *{red['name']}*")

    def force_everyone_blue(self, data):
        # force all players blue so refresh makes you zombie
        for player_id in self.humans:
            self.group.set_team(Team.BLUE, player_id)
        self.group.send_chat("\n".join((
            "Welcome to zomball. Huballs: stay alive, Zomballs: tag huballs",
            "If a zomball tags you, refresh to automatically turn zombie."
        )))

    def apply_base_settings(self):
        self.group.set_settings(
            selfAssignment="false",
            blueTeamName="Zomballs",
            redTeamName="Huballs",
        )
        for spec in self.spectators:
            self.group.set_team(Team.SPECTATORS, spec)

    def sample_preset(self):
        return random.choice(open(self.presets_path).readlines()).strip()

    def setup_and_launch_game(self):
        # prepare map, and move all to red except 1 then launch
        time.sleep(5)
        self.group.set_preset(self.sample_preset())
        human_ids = self.humans
        zombie_id = self.first_tagged_id
        if zombie_id not in human_ids:
            zombie_id = random.choice(list(human_ids))
        self.first_tagged_id = None
        self.group.set_team(Team.BLUE, zombie_id)
        for player_id in human_ids - {zombie_id}:
            self.group.set_team(Team.RED, player_id)
        time.sleep(5)
        self.group.launch_game()
        time.sleep(3)

    def handle_group(self):
        if not self.group.loaded:
            return
        self.apply_base_settings()
        if not self.group.game_active:
            self.group.end_game()
            if len(self.humans) >= 2:
                self.setup_and_launch_game()


if __name__ == "__main__":
    ZombiesBot(presets_path="zomb_presets.txt").daemon()
