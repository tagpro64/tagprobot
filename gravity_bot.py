from difflib import SequenceMatcher
from urllib.request import urlopen, Request
import csv
import datetime as dt
import functools
import io
import json
import random
import time

from tagpro_core import TagProCore, Team


def lru_cache_6hrs(func):
    # Cache the response for 6 hours without requesting again,
    # useful for expensive webserver requests including to google sheets and gltp wr json
    cached = functools.lru_cache(maxsize=128)(lambda *args, _p, **kwargs: func(*args, **kwargs))
    return lambda *args, **kwargs: cached(*args, _p=int(time.time() // 21600), **kwargs)


##################################
# Retrieve WRs / Send Replay Stats
##################################


@lru_cache_6hrs
def get_records():
    req = Request("https://gltp.fwotagprodad.workers.dev/records", headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=10) as res:
        return json.load(res)


def get_wr_entry(map_id):
    """Load wr for map_id from records endpoint."""
    entries = (
        entry for entry in get_records()
        if entry and entry["map_id"] == map_id and entry["record_time"]
    )
    return min(entries, key=lambda entry: entry["record_time"], default=None)


def upload_replay_uuid(uuid):
    upload_url = "https://gltp.fwotagprodad.workers.dev/delayed-upload"
    data = json.dumps({"input": uuid, "origin": "python"}).encode()
    req = Request(upload_url, data, {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=15) as res:
        return json.load(res)


#######################
# Retrieve Maps Details
#######################


def inject_map_id_into_preset(preset, map_id):
    digits = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
    n = int(map_id)
    enc = digits[0] if n == 0 else ""
    while n:
        n, r = divmod(n, 52)
        enc = digits[r] + enc
    inner = "f" + enc
    inj = "M" + digits[len(inner)] + inner
    pos = preset.find("M")
    if pos == -1:
        return preset
    old_len = digits.index(preset[pos + 1])
    return preset[:pos] + inj + preset[pos + 2 + old_len:]


def get_map_id(preset):
    d = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
    i = preset.find("M")
    if i < 0:
        return None
    n = 0
    for c in preset[i + 3:i + 2 + d.find(preset[i + 1])]:
        n = n * 52 + d.find(c)
    return n


@lru_cache_6hrs
def get_maps():
    url = (
        "https://docs.google.com/spreadsheets/d/"
        "1OnuTCekHKCD91W39jXBG4uveTCCyMxf9Ofead43MMCU/export"
        "?format=csv&id=1OnuTCekHKCD91W39jXBG4uveTCCyMxf9Ofead43MMCU"
        "&gid=1775606307"
    )
    with urlopen(url) as response:
        csv_file = io.StringIO(response.read().decode(), newline="")
    map_data = [
        {
            "name": conf["Map / Player"],
            "preset": conf["Group Preset"],
            "difficulty": conf["Final Rating"],
            "fun": conf["Final Fun \nRating"],
            "category": conf["Category"],
            "map_id": conf["Map ID"],
            "equivalent_map_ids": conf["Pseudo \nMap ID"].split(","),
            "caps_to_win": conf["Num\nof caps"],
            "allow_blue_caps": conf["Allow Blue Caps"].strip() == "TRUE",
            "balls_req": conf["Min\nBalls \nRec"],
            "max_balls_rec": conf["Max\nBalls\nRec"]
        }
        for conf in csv.DictReader(csv_file)
        if conf["Group Preset"].strip()
    ]
    illegal_maps = [
        m for m in map_data if
        not m["map_id"] or
        inject_map_id_into_preset(m["preset"], m["map_id"]) != m["preset"]
    ]
    print("illegal maps:", illegal_maps)
    illegal_map_ids = {m["map_id"] for m in illegal_maps}
    return [m for m in map_data if m["map_id"] not in illegal_map_ids]


def map_details_str(preset):
    if preset is None:
        return "No current game preset set."

    details = next((m for m in get_maps() if m["preset"] == preset), None)
    if details is None:
        return f"Sorry, I don't know the MAP details for {preset}"

    lines = [
        f"Playing '{details['name']}', Difficulty: {details['difficulty']},",
        f"Map ID: {details['map_id']}, Preset: {details['preset']}",
    ]
    if fnum(details["balls_req"], 100) > 1.0:
        lines.append(f"YOU NEED {details['balls_req']} BALLS TO COMPLETE THIS MAP!")

    wr = get_wr_entry(details["map_id"])
    if not wr:
        lines.append("(No world record less than 60 minutes recorded for this map)")
        return "\n".join(lines)

    total_seconds, ms = divmod(int(wr["record_time"]), 1000)
    minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    recorded_at = dt.datetime.fromtimestamp(wr["timestamp"] / 1000)
    elapsed = int((dt.datetime.now() - recorded_at).total_seconds())
    age = next(
        f"{elapsed // scale} {unit} ago"
        for limit, scale, unit in (
            (60, 1, "seconds"),
            (3600, 60, "minutes"),
            (86400, 3600, "hours"),
            (float("inf"), 86400, "days"),
        )
        if elapsed < limit
    )
    players = wr["players"]
    player_count = players if isinstance(players, int) else len(players)

    lines.append(
        f"WR: {f'{hours:02d}:' if hours else ''}{minutes:02d}:{seconds:02d}.{ms:03d} "
        f"(Cap by {wr['capping_player']}) "
        f"{'(Solo)' if player_count == 1 else f'+{player_count} others'} "
        f"| {age} | ({details['caps_to_win'] or 1} cap(s) to finish)"
    )
    if wr["capping_player_quote"]:
        lines.append(f"WR Quote: '{wr['capping_player_quote']}'")
    return "\n".join(lines)


##########
# Game Bot
##########


def fnum(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class GravityBot(TagProCore):
    def __init__(self, config):
        self.config = config
        super().__init__(name="grav_bot")
        self.settings = dict(config["map_defaults"])
        self.current_preset = None
        self.current_game_preset = self.launching_preset = None

        values = {
            "discord_link": config.get("discord_link", ""),
            "map_count": len(get_maps()),
        }
        self.periodic_messages = [
            (item if isinstance(item, str) else item["text"]).format(**values)
            for item in config["periodic_messages"]
            for _ in range(1 if isinstance(item, str) else item.get("weight", 1))
        ]

        self.group.hook("chat", self.handle_chat, keys=("message", "from", "auth"))
        self.group.hook("game", self.handle_game, keys="gameId")
        self.group.game.hook(
            "clientInfo",
            upload_replay_uuid,
            keys="gameUuid",
            required="gameUuid",
        )
        self.group.game.hook(
            "time",
            lambda _data: self.group.send_chat(map_details_str(self.current_game_preset)),
            match={"state": 1},
        )
        self.group.register_task(self.handle_group, 2)
        self.group.register_task(
            lambda: self.group.send_chat(random.choice(self.periodic_messages)),
            config.get("periodic_seconds", 1800),
        )
        self.join_or_create_group(config["room_name"], config.get("group_id"))

    @property
    def num_ready_balls(self):
        players = self.group.players.values()
        return sum(player.get("team") == Team.RED for player in players)

    def handle_group(self):
        if not self.group.loaded:
            return
        self.group.set_settings(
            groupName=self.config["room_name"],
            serverSelect="false",
            regions=self.config["lobby_defaults"]["region"],
            discoverable="true",
            isPrivate="true",
        )
        self.group.set_team(Team.SPECTATORS)

        if len(self.group.players) == 1:
            self.settings = dict(self.config["map_defaults"])
            self.current_preset = self.launching_preset = None
            return

        if not (self.current_preset or self.launching_preset):
            maps = self.legal_maps(self.settings)
            if not maps:
                self.settings = dict(self.config["map_defaults"])
                maps = self.legal_maps(self.settings)
            if maps:
                self.current_preset = random.choice(maps)["preset"]
                self.group.set_preset(self.current_preset)
        self.maybe_launch()

    def handle_game(self, game_id):
        if game_id is not None:
            self.current_game_preset = self.launching_preset
            self.launching_preset = None
        elif self.current_game_preset:
            self.group.send_chat(self.config["end_message"])
            self.current_game_preset = None

    def handle_chat(self, msg, sender, auth):
        if msg in self.config["ignored_messages"]:
            return
        if sender is None:
            if "has joined the group" in msg:
                self.group.send_chat(self.config["welcome_message"])
            return

        reply = self.handle_command(sender, auth, msg.strip())
        if reply:
            self.group.send_chat(reply)

    def handle_command(self, sender, auth, text):
        command, _, arg = text.partition(" ")

        if command == "LAUNCHNEW":
            if auth and sender in self.config["launchnew_names"]:
                self.launchnew(arg)
            return None
        if command == "HELP":
            return self.config["help_message"]
        if command == "MAP":
            return map_details_str(self.current_game_preset)
        if command == "INFO":
            query = arg.strip().lower()
            return random.choice(self.periodic_messages) if not query else max(
                self.periodic_messages,
                key=lambda message: SequenceMatcher(None, query, message.lower()).ratio(),
            )
        if command == "REGION":
            region_map = self.config["region_map"]
            regions = {
                **{region.lower(): region for region in region_map.values()},
                **{key.lower(): value for key, value in region_map.items()},
            }
            region = regions.get(arg.strip().lower())
            if not region:
                return "Invalid region selection"
            self.config["lobby_defaults"]["region"] = region
            self.group.set_setting("regions", region)
            return "Updated region."
        if command == "MODERATE":
            if auth and sender in self.config["moderator_names"]:
                for player_id, player in self.group.players.items():
                    if player.get("name") == sender and player.get("auth"):
                        self.group.emit("leader", player_id)
                        message = self.config["moderate_message"]
                        return message.format(sender=sender)
            return "Not authorized"
        if command == "SETTINGS":
            self.current_preset = self.launching_preset = None
            return self.update_settings(arg)

    def launchnew(self, message):
        parts = message.split()
        if len(parts) == 1:
            preset = self.config["preset_mapping"].get(parts[0], parts[0])
        elif len(parts) == 2 and fnum(parts[1]) is not None:
            preset = inject_map_id_into_preset(parts[0], parts[1])
        else:
            return
        illegal = preset and get_map_id(preset) in self.config["illegal_maps"]
        if not preset or not preset.startswith("gZ") or illegal:
            return

        self.current_preset = preset
        self.group.set_preset(preset)
        launched = self.maybe_launch(end_current=True)
        if launched and self.group.game_active:
            self.group.send_chat("Ending current game...")

    def update_settings(self, arg):
        parts = arg.split()
        if not parts:
            return (
                f"Current settings: {self.settings}\n"
                "Update settings via SETTINGS <key> <value>"
            )

        key = parts[0].lower()
        if len(parts) == 1 and key == "default":
            self.settings = dict(self.config["map_defaults"])
            return "Default settings applied"

        if len(parts) not in (2, 3) or key not in self.settings:
            return "Invalid command"

        value = parts[1] if len(parts) == 2 else parts[1:]
        if len(parts) == 2 and value.lower() in ("none", "any"):
            value = None

        new_settings = {**self.settings, key: value}
        maps = self.legal_maps(new_settings)

        if not maps:
            return f"No maps legal with settings: {new_settings}"

        self.settings = new_settings
        return (
            f"Updated settings to: {self.settings}\n"
            f"{len(maps)} legal maps with these settings"
        )

    def legal_maps(self, settings):
        category = (settings["category"] or "").lower()
        difficulty = settings["difficulty"]
        low, high = 0.0, 100.0
        if difficulty:
            low = float(difficulty[0] or 0.0)
            high = float(difficulty[1] or 100.0)
        ball_counts = {str(n) for n in range((self.num_ready_balls or 1) + 1)}
        min_fun = fnum(settings["minfun"], 0.0)
        return [
            m for m in get_maps()
            if category in m["category"].lower()
            and low <= fnum(m["difficulty"], 10) <= high
            and fnum(m["fun"], 100) >= min_fun
            and any(n in str(m.get("balls_req", "")) for n in ball_counts)
        ]

    def maybe_launch(self, *, end_current=False):
        if (
            self.launching_preset
            or self.group.ending_game
            or not self.current_preset
            or not self.num_ready_balls
            or (self.group.game_active and not end_current)
        ):
            return

        self.launching_preset = self.current_preset
        self.current_preset = None
        if end_current:
            self.group.end_game()
        self.group.launch_game()


if __name__ == "__main__":
    config = json.load(open("gravbot_config.json", encoding="utf-8"))
    GravityBot(config).daemon()
