import json
import os
import random
import re
import time

from rapidfuzz import fuzz

from maps import get_map_id, get_maps, inject_map_id_into_preset
from tagpro_core import TagProCore, Team, setup_logger

# Replay support intentionally disabled for now.
# from replay_manager import get_wr_entry, write_replay_uuid


INFO_RE = re.compile(r"^INFO(?:\s+(?P<query>.+))?$")
REGION_RE = re.compile(r"^REGION\s+(?P<region>.+)$")
SETTINGS_RE = re.compile(r"^SETTINGS(?:\s+.*)?$")
LAUNCHNEW_RE = re.compile(r"^LAUNCHNEW(?:\s+.*)?$")


event_logger = setup_logger("events_logger", "events.txt")


def fnum(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class GravityBot:
    commands = [
        (
            re.compile(r"^HELP$"),
            lambda bot, event, match: bot.config["help_message"],
        ),
        (re.compile(r"^MAP$"), lambda bot, event, match: bot.game_str),
        (INFO_RE, "info"),
        (REGION_RE, "region"),
        (re.compile(r"^MODERATE$"), "moderate"),
        (SETTINGS_RE, "settings_cmd"),
        (LAUNCHNEW_RE, "launchnew"),
        (
            re.compile(r"^(whos there|who's there|who is there)", re.I),
            lambda bot, event, match: "banana",
        ),
        (
            re.compile(r"^banana who", re.I),
            lambda bot, event, match: "knock knock",
        ),
    ]

    def __init__(self, config):
        self.config = config
        self.core = TagProCore(
            base_url=config.get("base_url", "https://tagpro.koalabeast.com"),
            cookie=os.getenv("TAGPRO_COOKIE"),
        )

        self.settings = dict(config["map_defaults"])
        self.current_preset = None
        self.current_game_preset = None
        self.spectating = False
        self.was_game_active = False
        self.last_touch = 0.0
        self.periodic_messages = self.expand_messages(config["periodic_messages"])

    def expand_messages(self, items):
        values = {
            "discord_link": self.config.get("discord_link", ""),
            "map_count": len(get_maps()),
        }
        messages = []

        for item in items:
            text, weight = (
                (item, 1)
                if isinstance(item, str)
                else (item["text"], item.get("weight", 1))
            )
            messages.extend([text.format(**values)] * weight)

        return messages

    def setup_group(self):
        if self.core.group is None:
            group = self.core.join_or_create_group(self.config["room_name"])
            group.on_chat(self.handle_chat)
            self.configure_group(group)
        return self.core.group

    def configure_group(self, group):
        for name, value in (
            ("groupName", self.config["room_name"]),
            ("serverSelect", "false"),
            ("regions", self.config["lobby_defaults"]["region"]),
            ("discoverable", "true"),
        ):
            group.set_setting(name, value)

    @property
    def game_str(self):
        if self.current_game_preset is None:
            return "No current game preset set."

        details = next(
            (m for m in get_maps() if m["preset"] == self.current_game_preset),
            None,
        )
        if details is None:
            return f"Sorry, I don't know the MAP details for {self.current_game_preset}"

        lines = [
            f"Playing '{details['name']}', Difficulty: {details['difficulty']},",
            f"Map ID: {details['map_id']}, Preset: {details['preset']}",
        ]

        if fnum(details["balls_req"], 100) > 1.0:
            lines.append(f"YOU NEED {details['balls_req']} BALLS TO COMPLETE THIS MAP!")

        wr = None
        if not wr:
            lines.append("(No world record less than 60 minutes recorded for this map)")

        return "\n".join(lines)

    def process_group(self):
        group = self.setup_group()

        if not self.spectating and group.my_id is not None:
            group.set_team(Team.SPECTATORS)
            self.spectating = True

        if not group.is_private:
            group.set_pug()

        now = time.monotonic()
        if now - self.last_touch >= 25:
            group.emit("touch", "page")
            self.last_touch = now

        if group.consume_lobby_changed():
            event_logger.info("(Red) Ready balls: %s", group.num_ready_balls)
            event_logger.info("Lobby Players: %s", group.lobby_players)
            if group.num_in_lobby == 1:
                self.settings = dict(self.config["map_defaults"])
                event_logger.info("Empty lobby, reverting to default settings.")

        if group.game_active and not self.was_game_active:
            event_logger.info("Game Running: %s", self.current_game_preset)
        elif not group.game_active and self.was_game_active:
            event_logger.info("End of game: %s", self.current_game_preset)
            group.send_chat(self.config["end_message"])

        self.was_game_active = group.game_active

    def handle_chat(self, event):
        msg = event.get("message", "")
        sender = event.get("from")

        if msg in self.config["ignored_messages"]:
            return

        event_logger.info("Chat: %s", event)

        if sender is None:
            if "has joined the group" in msg:
                time.sleep(1)
                self.core.group.send_chat(self.config["welcome_message"])
            return

        text = msg.strip()

        if text.startswith("LAUNCHNEW") and not self.can_launch(event):
            return

        for pattern, method in self.commands:
            match = pattern.match(text)
            if match:
                reply = (
                    method(self, event, match)
                    if callable(method)
                    else getattr(self, method)(event, match)
                )
                if reply:
                    self.core.group.send_chat(reply)
                return

    def can_launch(self, event):
        sender = event.get("from")
        return (
            event.get("auth")
            and sender in self.config["launchnew_names"]
            and sender in self.core.group.authed_members
        )

    def info(self, event, match):
        query = match.group("query")
        if not query:
            return random.choice(self.periodic_messages)

        random.shuffle(self.periodic_messages)
        return max(
            self.periodic_messages,
            key=lambda msg: fuzz.partial_ratio(query.lower(), msg.lower()),
        )

    def region(self, event, match):
        requested = match.group("region").strip().lower()
        by_name = {
            region.lower(): region
            for region in self.config["region_map"].values()
        }
        region = self.config["region_map"].get(requested) or by_name.get(requested)

        if not region:
            return "Invalid region selection"

        self.core.group.set_setting("regions", region)
        self.core.group.send_chat("Updated region.")

    def moderate(self, event, match):
        sender = event.get("from")

        if (
            event.get("auth")
            and sender in self.config["moderator_names"]
            and sender in self.core.group.authed_members
        ):
            self.core.group.emit("leader", self.core.group.authed_members[sender])
            return self.config["moderate_message"].format(sender=sender)

        return "Not authorized"

    def settings_cmd(self, event, match):
        self.handle_settings(event.get("message", ""))
        self.load_random_preset()

    def launchnew(self, event, match):
        parts = event.get("message", "").split()
        preset = (
            self.config["preset_mapping"].get(parts[1], parts[1])
            if len(parts) == 2
            else None
        )

        if len(parts) == 3 and fnum(parts[2]):
            preset = inject_map_id_into_preset(parts[1], parts[2])

        if (
            not preset
            or not preset.startswith("gZ")
            or get_map_id(preset) in self.config["illegal_maps"]
        ):
            return

        self.core.group.send_chat("Ending current game...")
        time.sleep(2)
        self.core.group.emit("endGame")
        self.load_preset(preset)
        time.sleep(2)
        self.maybe_launch()

    def handle_settings(self, message):
        parts = message.strip().split()

        if message.strip() == "SETTINGS":
            self.core.group.send_chat("Current settings: " + str(self.settings))
            self.core.group.send_chat("Update settings via SETTINGS <key> <value>")
            return

        if message.strip().lower() == "settings default":
            self.settings = dict(self.config["map_defaults"])
            self.core.group.send_chat("Default settings applied")
            return

        if len(parts) not in (3, 4) or parts[1].lower() not in self.settings:
            self.core.group.send_chat("Invalid command")
            return

        key = parts[1].lower()
        value = parts[2] if len(parts) == 3 else parts[2:]
        value = (
            None
            if isinstance(value, str) and value.lower() in ("none", "any")
            else value
        )

        new_settings = {**self.settings, key: value}
        maps = self.legal_maps(new_settings)

        if not maps:
            self.core.group.send_chat(f"No maps legal with settings: {new_settings}")
            return

        self.settings = new_settings
        self.core.group.send_chat(f"Updated settings to: {self.settings}")
        self.core.group.send_chat(f"{len(maps)} legal maps with these settings")

    def legal_maps(self, settings):
        maps = get_maps()

        if settings["category"]:
            maps = [
                m for m in maps
                if settings["category"].lower() in m["category"].lower()
            ]

        if settings["difficulty"]:
            low = float(settings["difficulty"][0] or 0.0)
            high = float(settings["difficulty"][1] or 100.0)
            maps = [
                m for m in maps
                if low <= fnum(m["difficulty"], 10) <= high
            ]

        return [
            m for m in maps
            if fnum(m["fun"], 100) >= fnum(settings["minfun"], 0.0)
            and any(
                str(n) in str(m.get("balls_req", ""))
                for n in range((self.core.group.num_ready_balls or 1) + 1)
            )
        ]

    def load_random_preset(self):
        maps = self.legal_maps(self.settings)

        if not maps:
            self.settings = dict(self.config["map_defaults"])
            maps = self.legal_maps(self.settings)

        if maps:
            self.load_preset(random.choice([m["preset"] for m in maps]))

    def load_preset(self, preset):
        self.core.group.emit("groupPresetApply", preset)
        self.current_preset = preset
        event_logger.info("Set preset: %s", preset)

    def maybe_launch(self):
        if (
            self.core.group.game_active
            or not self.core.group.num_ready_balls
            or self.current_preset is None
        ):
            return False

        self.current_game_preset = self.current_preset
        self.current_preset = None
        self.core.group.launch_game()
        event_logger.info("Launched preset: %s", self.current_game_preset)
        time.sleep(5)
        return True

    def run(self):
        tick = 1
        launched_new = False

        while True:
            time.sleep(1)
            self.process_group()

            if launched_new:
                time.sleep(5)
                self.core.group.send_chat(self.game_str)
                launched_new = False

            if tick % self.config.get("periodic_seconds", 1800) == 0:
                self.core.group.send_chat(random.choice(self.periodic_messages))

            if (
                tick % self.config.get("launch_check_seconds", 10) == 0
                and not self.core.group.game_active
                and self.core.group.num_in_lobby != 1
            ):
                self.load_random_preset()
                time.sleep(2)
                launched_new = self.maybe_launch()

            tick += 1


def load_config(path=None):
    path = path or os.getenv("BOT_CONFIG", "bot_config.json")
    with open(path, encoding="utf-8") as file:
        return json.load(file)


if __name__ == "__main__":
    GravityBot(load_config()).run()
