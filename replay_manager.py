import time
import requests
import json
import os
import tqdm

from maps import get_maps


def process_replays():
    get_maps()
    while True:
        try:
            update_replays()
        except RuntimeError as e:
            print(e)
            time.sleep(120)


def update_replays():
    is_updated = process_downloaded_replays(
        replay_stats_path="replay_stats2.json",
        replay_download_dir="./replays"
    )
    if is_updated:
        push_replay_stats_to_leaderboard(replay_stats_path="replay_stats2.json")

    # download bot replays (if not already downloaded)
    bot_logged_replays = [line.strip() for line in open("replay_uuids.txt").readlines() if line.strip()]
    download_replays(bot_logged_replays)
    # download manual entry replays (if not already downloaded)
    manual_entry_replays = [line.strip() for line in open("manual_replay_uuids.txt").readlines() if line.strip()]
    download_replays(manual_entry_replays)

    time.sleep(10)


def download_replays(uuids):
    downloaded = {f.name for f in os.scandir("./replays") if f.is_file()}
    try:
        attempts = json.load(open("download_attempts.json"))
    except FileNotFoundError:
        attempts = {}
    for uid in set(uuids) - downloaded:
        now = time.time()
        if uid not in attempts:
            attempts[uid] = {"first": now, "last": now}
        else:
            first, last = attempts[uid]["first"], attempts[uid]["last"]
            if last - first > 86400 or now - last <= (last - first) / 4:
                continue
            attempts[uid]["last"] = now
        r = get_replay_data(uid)
        if r:
            print("success for", uid)
            json.dump(r, open(f"./replays/{uid}", "w"))
        else:
            print("failure for", uid)
    json.dump(attempts, open("download_attempts.json", "w"))


def process_downloaded_replays(replay_stats_path, replay_download_dir):
    if os.path.exists(replay_stats_path):
        replay_stats = json.load(open(replay_stats_path))  # processed replays
    else:
        replay_stats = {}

    # process unprocessed replays
    downloaded_replays = [entry.name for entry in os.scandir(replay_download_dir) if entry.is_file()]
    unprocessed_downloaded_replay_uuids = set(downloaded_replays) - set(replay_stats.keys())
    new_replay_stats = {}
    if unprocessed_downloaded_replay_uuids:
        for replay_uuid in tqdm.tqdm(list(unprocessed_downloaded_replay_uuids)):
            try:
                new_stats = get_details(json.load(open(f"{replay_download_dir}/{replay_uuid}")))
            except json.decoder.JSONDecodeError as e:
                print("failure for", replay_uuid)
                raise e
            if new_stats is not None:
                new_replay_stats[replay_uuid] = new_stats

    replay_stats.update(new_replay_stats)
    json.dump(replay_stats, open(replay_stats_path, "w"))

    is_updated = bool(new_replay_stats)
    return is_updated


def push_replay_stats_to_leaderboard(replay_stats_path):
    data = json.load(open(replay_stats_path))
    data = list(data.values())

    spreadsheet_map_ids = set([m["map_id"] for m in get_maps()])

    data = [
        d for d in data
        if d["record_time"] is not None and  # filter out DNF
        d["map_id"] in spreadsheet_map_ids  # filter out maps not on spreadsheet
    ]

    data = sorted(data, key=lambda d: -d["timestamp"])

    response = requests.post(
        "https://worldrecords.bambitp.workers.dev/upload",
        params={"password": "supersecretpassword"},
        headers={"Content-Type": "application/json"},
        json=data,
    )
    print("Push to leaderboard status code:", response.status_code)


def get_wr_entry(map_id, replay_stats_path="replay_stats2.json"):
    """load wr for map_id from replay_stats2.json"""
    for _ in range(10):
        try:
            data = json.load(open(replay_stats_path))
        except json.decoder.JSONDecodeError:
            time.sleep(0.1)
            continue
        break
    map_entries = [entry for entry in data.values() if entry["map_id"] == map_id and entry and entry["record_time"]]
    if not map_entries:
        return None
    return min(
        map_entries,
        key=lambda e: e["record_time"]
    )


def get_details(replay):
    assert replay[0][1] == "recorder-metadata"
    assert replay[2][1] == "map"
    assert replay[3][1] == "clientInfo"
    metadata = replay[0][2]
    map_data = replay[2][2]
    try:
        map_id = replay[3][2]["mapfile"].split("/")[1] if replay[3][2]["mapfile"] else None
    except IndexError:
        map_id = None

    spreadsheet_maps = [m for m in get_maps() if m["map_id"] == map_id]
    assert len(spreadsheet_maps) in (0, 1)

    # check equivalent maps if no matches
    if not spreadsheet_maps:
        spreadsheet_maps = [m for m in get_maps() if str(map_id) in m["equivalent_map_ids"]]

    if spreadsheet_maps:
        if spreadsheet_maps[0].get("caps_to_win") == 'pups':
            caps_to_win = float("inf")
        else:
            caps_to_win = int(spreadsheet_maps[0].get("caps_to_win") or 1)
        effective_map_id = spreadsheet_maps[0]["map_id"]
        allow_blue_caps = bool(spreadsheet_maps[0]["allow_blue_caps"])
    else:
        caps_to_win = 1
        effective_map_id = map_id
        allow_blue_caps = False

    try:
        first_timer_ts = [r for r in replay if r[1] == 'time' and r[2]["state"] == 1][0][0]
    except IndexError:
        print("Invalid replay:", metadata["uuid"])
        return None

    players = {
        p["id"]: {"name": p["displayName"], "user_id": p["userId"], "is_red": p["team"] == 1}
        for p in metadata["players"]
    }

    def get_run_details():
        for cap_time, _, caps in [r for r in replay if r[1] == 'p']:
            for cap_details in caps:
                if cap_details.get('s-captures') != caps_to_win:
                    continue
                capping_player_in_game_id = cap_details["id"]
                capping_player = players[capping_player_in_game_id]
                if not (capping_player["is_red"] or allow_blue_caps):
                    continue
                record_time = cap_time - first_timer_ts
                capping_user_name, capping_user_id = capping_player["name"], capping_player["user_id"]
                capping_player_msgs = [
                    r for r in replay if
                    r[1] == 'chat' and r[2].get('from') == capping_player_in_game_id
                ]
                capping_player_quote = capping_player_msgs[-1][2]["message"] if capping_player_msgs else None
                total_jumps = sum([
                    (
                        r[1] == "replayPlayerMessage" and
                        r[2].get("type") == "sound" and
                        r[2].get("data", {}).get("s") == "jump" and
                        r[0] <= cap_time
                    )
                    for r in replay
                ])
                return record_time, capping_user_name, capping_user_id, capping_player_quote, total_jumps
        return None, None, None, None, None

    record_time, capping_user_name, capping_user_id, capping_player_quote, total_jumps = get_run_details()

    return {
        "map_id": effective_map_id,
        "actual_map_id": map_id,
        "preset": None,  # TODO
        "map_name": map_data["info"]["name"],
        "map_author": map_data["info"]["author"],
        "players": list(players.values()),
        "capping_player": capping_user_name,
        "capping_player_user_id": capping_user_id,
        "record_time": record_time,
        "is_solo": len(players) == 1,
        "timestamp": metadata["started"],
        "uuid": metadata['uuid'],
        "caps_to_win": caps_to_win,
        "capping_player_quote": capping_player_quote,
        "total_jumps": total_jumps,
    }


def write_replay_uuid(uuid):
    with open("replay_uuids.txt", "a") as f:
        f.write("\n" + uuid.strip())


def get_replay_data(uuid):
    time.sleep(5)
    response = requests.get(
        "https://tagpro.koalabeast.com/replays/data",
        params={"uuid": uuid}
    )
    if response.status_code == 429:
        raise RuntimeError
    try:
        data = response.json()
    except requests.exceptions.JSONDecodeError:
        raise RuntimeError
    if isinstance(data, list) or len(data.get("games", [])) != 1:
        return None
    response = requests.get(
        "https://tagpro.koalabeast.com/replays/gameFile",
        params={"gameId": data["games"][0]["id"]}
    )
    data = [json.loads(line) for line in response.text.splitlines()]

    if not [r for r in data if r[1] == 'time' and r[2]["state"] == 1]:
        return None

    return data


if __name__ == "__main__":
    process_replays()


"""
TODO:

- Gates unlocked stats
"""
