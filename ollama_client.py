import httpx

from prompts import SYSTEM_PROMPT


class OllamaClient:
    """Thin wrapper around Ollama's /api/chat for this bot's single support persona."""

    def __init__(self, host: str, model: str, api_key: str | None = None):
        self._host = host.rstrip("/")
        self._model = model
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = httpx.AsyncClient(timeout=60.0, headers=headers)

    async def close(self) -> None:
        await self._client.aclose()

    async def reply(self, history: list[dict]) -> str:
        messages = [{"role": "system", "content": SYSTEM_PROMPT}, *history]
        payload = {"model": self._model, "messages": messages, "stream": False}
        response = await self._client.post(f"{self._host}/api/chat", json=payload)
        response.raise_for_status()
        return response.json()["message"].get("content", "")
