import requests

class Conversation:
    api_key: str
    google_enabled: str
    engine: str
    enable_memory: bool
    host: str = "https://api.writesonic.com"
    timeout: int = 90
    history: list = []

    def __init__(
        self,
        *,
        api_key,
        google_enabled: bool = True,
        enable_memory: bool = False,
        engine: str = "premium",
        timeout: int = 90,
    ) -> None:
        self.api_key = api_key
        self.google_enabled = google_enabled
        self.engine = engine
        self.enable_memory = enable_memory
        self.timeout = timeout

    def send_message(self, *, message: str, history_data: list = []) -> dict:
        headers = {
            "accept": "application/json",
            "content-type": "application/json",
            "X-API-KEY": self.api_key,
        }
        payload = {
            "input_text": message,
            "history_data": history_data,
            "enable_google_results": self.google_enabled,
            "enable_memory": self.enable_memory,
        }
        params = {
            "engine": self.engine,
        }
        if self.enable_memory:
            payload["history_data"] = history_data if len(history_data) > 0 else self.history
       
        data = requests.post(
            f"{self.host}/v2/business/content/chatsonic",
            headers=headers,
            json=payload,
            params=params,
            timeout=90,
        ).json()
        history_data.append({"is_sent":True,"message":message})
        history_data.append({"is_sent":False,"message":data.get("message")})
        self.history  = history_data
        return data