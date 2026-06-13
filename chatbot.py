from tagpro_core import TagProCore


class ChatBot(TagProCore):
    def __init__(self):
        super().__init__(name="chat")
        self.join_or_create_group("💬 Chat 💬")


if __name__ == "__main__":
    ChatBot().daemon()
