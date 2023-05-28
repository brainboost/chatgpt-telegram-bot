from abc import ABC, abstractmethod


class EngineInterface(ABC):
    @abstractmethod
    def ask(self, text, userConfig: dict) -> str:
        pass

    @abstractmethod
    def reset_chat(self):
        pass

    @abstractmethod
    def close(self):
        pass

    @property
    @abstractmethod
    def engine_type(self) -> str:
        pass
