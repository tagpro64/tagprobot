import requests
import time
import functools
import io
import csv


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


def get_map_id(p):
    d = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
    i = p.find("M")
    if i < 0:
        return None
    n = 0
    for c in p[i+3:i+2+d.find(p[i+1])]:
        n=n*52+d.find(c)
    return n


def lru_cache_6hrs(func):
    cached = functools.lru_cache(maxsize=128)(lambda *args, _p, **kwargs: func(*args, **kwargs))
    return lambda *args, **kwargs: cached(*args, _p=int(time.time() // 21600), **kwargs)


@lru_cache_6hrs
def get_maps():
    response = requests.get(
        "https://docs.google.com/spreadsheets/d/1OnuTCekHKCD91W39jXBG4uveTCCyMxf9Ofead43MMCU/export",
        params={
            "format": "csv",
            'id': '1OnuTCekHKCD91W39jXBG4uveTCCyMxf9Ofead43MMCU',
            'gid': '1775606307',
        }
    )
    csv_file = io.StringIO(response.text, newline="")
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
        not m["preset"].strip() or
        not m["map_id"] or
        inject_map_id_into_preset(m["preset"], m["map_id"]) != m["preset"]
    ]
    print("illegal maps:", illegal_maps)
    return [m for m in map_data if m["map_id"] not in [im["map_id"] for im in illegal_maps]]
