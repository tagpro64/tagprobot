import time

from tagpro_core import TagProCore


class KickBot:
    def __init__(self):
        self.group = TagProCore().join_or_create_group("💬 Chat 💬")

    def run(self):
        while True:
            time.sleep(60)


if __name__ == "__main__":
    KickBot().run()
