from abc import ABC, abstractmethod
from enum import Enum


class Engines(str, Enum):
    BING = 1
    CHATGPT = 2
    CHATSONIC = 3
    BARD = 4


class EngineInterface(ABC):
    @abstractmethod
    def ask(self, text, userConfig: dict) -> str:
        pass
    
    @abstractmethod
    async def ask_async(self, text, userConfig: dict) -> str:
        pass
    
    @abstractmethod
    def reset_chat(self):
        pass
    
    @abstractmethod
    def close(self):
        pass    

    @property
    @abstractmethod
    def engine_type(self) -> Engines:
        raise NotImplementedError
