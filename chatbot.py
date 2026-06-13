import time

from tagpro_core import TagProCore


class ChatBot:
    def __init__(self):
        self.group = TagProCore(name="chat").join_or_create_group("💬 Chat 💬")

    def run(self):
        while True:
            time.sleep(60)


if __name__ == "__main__":
    ChatBot().run()
