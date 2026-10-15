"""Scripted stand-ins for ART + the OpenAI client so vm/train_art.py can be checked on a Mac with no GPU.
The fake model returns the reference edit for every other task and garbage otherwise, exercising both reward branches."""
from types import SimpleNamespace


class FakeTrajectory:
    def __init__(self, messages_and_choices, reward, metadata):
        self.messages_and_choices, self.reward, self.metadata, self.metrics = list(messages_and_choices), reward, metadata, {}

    def messages(self):
        return [m if isinstance(m, dict) else {"role": "assistant", "content": m.message.content} for m in self.messages_and_choices]


class FakeArt:
    Trajectory = FakeTrajectory


class FakeModel:
    def get_inference_name(self):
        return "fake"


class _Completions:
    def __init__(self, tasks):
        self.sol = {t.prompt: t.solution for t in tasks}
        self.n = 0

    async def create(self, model, messages, temperature, max_completion_tokens):
        self.n += 1
        req = messages[-1]["content"].rsplit("Request: ", 1)[1]
        text = self.sol.get(req, "") if self.n % 2 else "make it better please"
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text, tool_calls=None), finish_reason="stop")])


class FakeClient:
    def __init__(self, tasks):
        self.chat = SimpleNamespace(completions=_Completions(tasks))
