import http.cookiejar
import logging
from pathlib import Path
import signal
import re
import threading
import urllib.parse
import urllib.request
from collections import deque
from enum import IntEnum
import warnings
import time

import socketio


def setup_logger(name, path):
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)

    if not logger.handlers:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(path)
        formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s", "%Y-%m-%d %H:%M:%S")
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    return logger


class Team(IntEnum):
    RED = 1
    BLUE = 2
    SPECTATORS = 3
    WAITING = 4


class WebSocketHandler:
    def __init__(self, ws_name=None, **connection):
        self.event_handlers, self._tasks = [], []
        self._emit_lock = threading.RLock()
        self.ws_log = setup_logger(ws_name, f"logs/{ws_name}.txt") if ws_name else None
        self.socket = None
        self.set_connection(**connection)

    def set_connection(self, *, base_url=None, namespace=None, cookie="", query=None, namespaces=None):
        with self._emit_lock:
            if self.socket and self.socket.connected:
                self.socket.disconnect()
            self.base_url = base_url.rstrip("/") if base_url else None
            self.namespace = namespace
            self.cookie = cookie or ""
            self.query = query or {}
            self.namespaces = namespaces or ([namespace] if namespace else [])
            self.socket = socketio.Client(
                reconnection=False, logger=False, engineio_logger=False, handle_sigint=False
            )
            for namespace in self.namespaces:
                self.socket.on("*", self._receive, namespace=namespace)
        return self

    def clear_connection(self):
        return self.set_connection()

    @property
    def connected(self):
        return self.socket.connected and all(namespace in self.socket.namespaces for namespace in self.namespaces)

    def connect(self):
        with self._emit_lock:
            if self.connected or not self.base_url or not self.namespace:
                return self
            url = self.base_url if not self.query else f"{self.base_url}?{urllib.parse.urlencode(self.query)}"
            try:
                self.socket.connect(
                    url,
                    headers={"Origin": self.base_url, "Cookie": self.cookie},
                    namespaces=self.namespaces,
                    transports=["websocket"],
                    wait_timeout=10,
                )
                for task in self._tasks:
                    self.socket.start_background_task(task, self.socket)
            except (socketio.exceptions.ConnectionError, ValueError):
                pass
        return self

    def register_task(self, task, interval):
        def loop(socket):
            while socket is self.socket and self.connected:
                task()
                socket.sleep(interval)
        self._tasks.append(loop)
        if self.connected:
            self.socket.start_background_task(loop, self.socket)
        return task

    def emit(self, name, *args):
        with self._emit_lock:
            if not self.connect().connected:
                return False
            payload = () if not args else (args[0] if len(args) == 1 else args,)
            self.socket.emit(name, *payload, namespace=self.namespace)
            return True

    def hook(self, event, handler=None, *args, keys=None, required=(),
             match=None, dispatch=None, **kwargs):
        keys = (keys,) if isinstance(keys, str) else keys
        events = event if isinstance(event, (set, tuple)) else (event,)
        match = (match or {}).items()
        required = (required,) if isinstance(required, str) else required or ()

        def run(name, data):
            data_dict = data if isinstance(data, dict) else {}
            if not (name in events or callable(event) and event(name, data)):
                return False
            if any(not data_dict.get(key) for key in required):
                return False
            if any(data_dict.get(key) != value for key, value in match):
                return False
            if handler is not None:
                get = data.get if isinstance(data, dict) else lambda _key: data
                values = (data,) if keys is None else tuple(get(key) for key in keys)
                result = handler(*values, *args, **kwargs)
                if dispatch and result is not None:
                    self._dispatch_event(*dispatch)
            return True

        self.event_handlers.append(run)
        return handler

    def _receive(self, name, *args):
        data = args[0] if len(args) == 1 else list(args) if args else None
        handled = self._dispatch_event(name, data)
        if self.ws_log:
            self.ws_log.info("%s: %s %s", "" if handled else "UNHANDLED", name, data)

    def _dispatch_event(self, name, data):
        handled = False
        for run in self.event_handlers:
            handled = run(name, data) or handled
        return handled


class TagProSession:
    def __init__(self, *, base_url, cookie=None):
        self.base_url = base_url.rstrip("/")
        self.initial_cookie = cookie

        self.cookies = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cookies)
        )

    @property
    def cookie_header(self):
        parts = [self.initial_cookie] if self.initial_cookie else []
        parts.extend(
            f"{cookie.name}={cookie.value}"
            for cookie in self.cookies
        )
        return "; ".join(parts)

    def request(self, method, path, data=None, *, send_cookies=True):
        body = (
            urllib.parse.urlencode(data).encode()
            if data is not None
            else None
        )
        cookie = self.cookie_header if send_cookies else ""
        headers = {"Cookie": cookie} if cookie else {}

        if body is not None:
            headers.update({
                "Content-Type": "application/x-www-form-urlencoded",
                "Origin": self.base_url,
                "Referer": f"{self.base_url}/groups/",
            })

        request = urllib.request.Request(
            urllib.parse.urljoin(self.base_url + "/", path),
            data=body,
            headers=headers,
            method=method.upper(),
        )
        open_url = self.opener.open if send_cookies else urllib.request.urlopen

        with open_url(request, timeout=15) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return (
                response.read().decode(charset, "replace"),
                response.geturl(),
            )


class TagProCore:
    _instances = []

    def __init__(self, *, name=None, base_url="https://tagpro.koalabeast.com", cookie=None):
        self.session = TagProSession(base_url=base_url, cookie=cookie)
        self.group = GroupManager(session=self.session, name=name)
        self._instances.append(self)

        for signum in (signal.SIGINT, signal.SIGTERM):
            signal.signal(signum, self.cleanup_all)

    def cleanup(self):
        self.group.game.clear_connection()
        self.group.clear_connection()
        self.session.request("GET", "/groups/leave")

    @classmethod
    def cleanup_all(cls, signum, _frame):
        for core in cls._instances:
            core.cleanup()
        raise SystemExit(128 + signum)

    def join_or_create_group(self, room_name, group_id=None):
        if self.group.group_id is not None:
            return self.group

        group_id = group_id or next(
            (group["id"] for group in self.list_groups() if group["name"] == room_name),
            None,
        ) or self.create_group(room_name)
        if not group_id:
            raise RuntimeError("Could not find or create TagPro group.")
        return self.join_group(group_id)

    def create_group(self, group_name, pug=True, public=True):
        data = {"name": group_name, "preset": ""}
        data.update(
            (key, "on")
            for key, enabled in (("public", public), ("private", pug))
            if enabled
        )
        html, final_url = self.session.request("POST", "/groups/create", data)
        return self._group_id(final_url) or self._group_id(html)

    def list_groups(self):
        html, _ = self.session.request("GET", "/groups/")

        def get(pattern, block, default=""):
            match = re.search(pattern, block, re.S)
            return self._clean_html(match.group(1)) if match else default

        return [{
            "id": self._group_id(block),
            "name": get(r'<div class="group-name">\s*(.*?)\s*</div>', block),
            "type": get(r'<div class="pull-right group-type">\s*(.*?)\s*</div>', block),
            "players": int(get(r"Players:\s*(\d+)", block, 0)),
            "members": [
                self._clean_html(member)
                for member in re.findall(
                    r'<div class="groupMember[^"]*">(.*?)</div>',
                    block,
                    re.S,
                )
            ],
        } for block in html.split('<div class="group-item">')[1:]]

    def join_group(self, group_id):
        _, group_url = self.session.request("GET", f"/groups/{group_id}")
        return self.group.join(group_url)

    @staticmethod
    def _group_id(url):
        match = re.search(r"/groups/([A-Za-z0-9]{8})(?=[/?#'\"\s<]|$)", url)
        return match.group(1) if match else None

    @staticmethod
    def _clean_html(value):
        return re.sub(r"\s+", " ", re.sub(r"<.*?>", "", value)).strip()

    def daemon(self):
        while True:
            time.sleep(1)


class GroupManager(WebSocketHandler):
    # Unhandled: "groupPlay", "groupPresetApply", "groupPresetResult", "leader",
    # "private", "pub", "pug", "servers", "touch",

    def __init__(self, session, name=None, group_url=None):
        self.session = session
        self.group_id = self.game_id = self.my_id = None
        self.loaded = self._joining_game = False
        self._end_action = None
        self.settings, self.players, self.chats = {}, {}, deque()
        self.game = GameManager(session=session, name=name)
        self.game.hook("end", self.end_game)
        super().__init__(ws_name=None if name is None else f"group_{name}")
        self.register_task(self._emit_touch, 2)
        self.register_task(self.handle_launched_game, 2)
        players = self.players
        set_player = players.setdefault
        self.hook("you", lambda data: setattr(self, "my_id", data))
        self.hook("loaded", setattr, self, "loaded", True, keys=())
        self.hook("setting", self.settings.__setitem__, keys=("name", "value"), required="name")
        self.hook(
            ("member", "team"),
            lambda data: set_player(data["id"], {"id": data["id"]}).update(data), required="id"
        )
        self.hook("removed", players.pop, None, keys="id")
        self.hook("game", self._set_game, keys="gameId")
        self.hook("game", self.handle_launched_game, keys=())
        self.hook("play", self.handle_launched_game, keys=(), force=True)
        self.hook("endGame", self._confirm_game_end)
        self.hook("endGame", self.game.clear_connection, keys=())
        if group_url is not None:
            self.join(group_url)

    @property
    def ending_game(self):
        return self._end_action is not None

    @property
    def game_active(self):
        return self.game_id is not None

    def join(self, group_url):
        path = urllib.parse.urlparse(group_url).path
        self.group_id = path.rsplit("/", 1)[-1]
        self.loaded = False
        self.my_id = self.game_id = None
        for state in (self.players, self.settings, self.chats):
            state.clear()
        self.set_connection(
            base_url=self.session.base_url,
            namespace=path,
            cookie=self.session.cookie_header,
        ).connect()
        return self

    def _emit_touch(self):
        self.emit("touch", "game" if self.game_active else "page")

    def send_chat(self, text):
        for line in text.split("\n"):
            self.emit("chat", line)

    def set_setting(self, name, value):
        self.emit("setting", {"name": name, "value": value})

    def set_settings(self, **kwargs):
        for k, v in kwargs.items():
            self.set_setting(k, v)

    def set_preset(self, preset):
        self.emit("groupPresetApply", preset)

    def launch_game(self):
        if self.ending_game:
            if self.game_active or self.game.connected:
                self._end_action = "launch"
                return
            self._end_action = None
        self.emit("groupPlay")

    def end_game(self, data=None, *, delay=0):
        if self.ending_game or not (self.game_active or self.game.connected):
            return False
        self._end_action = "end"
        self.socket.start_background_task(self._end_game, delay)
        return True

    def _end_game(self, delay=0):
        self.socket.sleep(delay)
        if not self.ending_game:
            return
        self.game_id = None
        self.game.clear_connection()
        self.emit("endGame")

    def set_team(self, team, player_id=None):
        player_id = self.my_id if player_id is None else player_id
        if player_id is None:
            return False
        return self.emit("team", {"id": player_id, "team": team})

    def set_pug(self, is_pug=True):
        self.emit("pug" if is_pug else "pub")

    def _set_game(self, game_id):
        if game_id is None:
            if self.ending_game:
                self._confirm_game_end()
            else:
                self.game_id = None
            return
        if not self.ending_game:
            self.game_id = game_id

    def _confirm_game_end(self, data=None):
        should_launch = self._end_action == "launch"
        self._end_action = None
        self.game_id = None
        if should_launch:
            self.emit("groupPlay")

    def handle_launched_game(self, *, force=False):
        blocked = self.ending_game or self.game.connected or self._joining_game
        if not self.loaded or blocked or not (force or self.game_active):
            return

        self._joining_game = True
        try:
            self.emit("touch", "joining")
            self.session.request("GET", "/games/find")

            regions = self.settings.get("regions") or []
            if isinstance(regions, str):
                regions = [regions]

            res = self.game.join_from_group(
                group_id=self.group_id,
                selections={
                    "regions": regions,
                    "gameModes": ["classic"],
                    "spectate": False,
                },
            )
            if res is None:
                self.end_game()
        finally:
            self._joining_game = False


class GameManager(WebSocketHandler):
    PLAYER_KEYS = ("name", "team", "s-pops", "s-tags")

    def __init__(self, session, name=None, game_url=None):
        self.session, self.name = session, name
        self.players, self.started = {}, False
        super().__init__(ws_name=None if name is None else f"game_{name}")
        players = self.players
        self.hook("time", setattr, self, "started", True, keys=(), match={"state": 1})
        self.hook("time")
        self.hook("p", self._handle_players)
        self.hook("playerLeft", players.pop, None, keys="id", dispatch=("gamePlayerUpdate", players))
        self.hook("end", self.clear_connection, keys=())
        if game_url is not None:
            self.join(game_url)

    def _handle_players(self, data):
        players = data.get("u", []) if isinstance(data, dict) else data
        if not isinstance(players, list):
            return True

        popped_once = lambda pl: pl.get("dead") is True and pl.get("s-pops") == 1
        red = next((self._merged_player(player) for player in players if popped_once(player)), {})
        blue = next((self._merged_player(player) for player in players if self._tag_incremented(player)), {})
        if red.get("team") == Team.RED and blue.get("team") == Team.BLUE:
            self._dispatch_event("tag", (blue, red))
        if sum(self._update_player(player) for player in players):
            self._dispatch_event("gamePlayerUpdate", self.players)
        return True

    def clear_connection(self):
        self.players.clear()
        self.started = False
        return super().clear_connection()

    def join(self, game_url):
        html, final_url = self.session.request("GET", game_url)
        parsed = urllib.parse.urlparse(final_url)
        match = re.search(r'tagproConfig\.gameSocket\s*=\s*"([^"]+)"', html)
        game_host = match.group(1) if match else parsed.netloc
        return self.set_connection(
            base_url=f"{parsed.scheme}://{game_host}",
            namespace="/",
            cookie=self.session.cookie_header,
        ).connect()

    def _merged_player(self, player):
        return self.players.get(player.get("id"), {}) | player

    def _tag_incremented(self, player):
        previous = self.players.get(player.get("id"), {})
        return player.get("s-tags", 0) > previous.get("s-tags", 0)

    def _update_player(self, player):
        if not isinstance(player, dict) or player.get("id") is None:
            return False

        data = {
            key: player[key]
            for key in self.PLAYER_KEYS
            if key in player
        }
        if not data:
            return False

        current = self.players.setdefault(player["id"], {})
        if all(current.get(key) == value for key, value in data.items()):
            return False

        current.update(data)
        return True

    def join_from_group(self, *, group_id, selections):
        self.clear_connection()
        found_game = threading.Event()
        game_urls = []
        joiner = WebSocketHandler(
            ws_name=None if self.name is None else f"joiner_{self.name}",
            base_url=self.session.base_url,
            namespace="/games/find",
            cookie=self.session.cookie_header,
            query={"group": group_id},
        )

        joiner.hook("ready", joiner.emit, "JoinerSelections", selections, keys=())
        joiner.hook("FoundWorld", game_urls.append, keys="url", required="url")
        joiner.hook("FoundWorld", found_game.set, keys=(), required="url")
        joiner.hook("game", game_urls.append, "/game", keys=(), required="gameId")
        joiner.hook("game", found_game.set, keys=(), required="gameId")

        try:
            if not (joiner.connect().connected and found_game.wait(15)):
                warnings.warn("Timed out waiting for TagPro game.", stacklevel=2)
                return None
            return self.join(game_urls[0])
        finally:
            joiner.clear_connection()
