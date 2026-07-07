"""OpenRouter Provider - Fallback 2 (20 RPM / 200 req/day free)"""
import asyncio
from typing import Optional, Dict
import aiohttp
from ..config import CONFIG
from ..logger import setup_logger

logger = setup_logger("OpenRouterLLM")

BASE_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "meta-llama/llama-3.3-70b-instruct:free"

class OpenRouterProvider:
    def __init__(self):
        self.api_key = CONFIG.openrouter_api_key
        self.available = bool(self.api_key)
        self.name = "openrouter"
        self.daily_used = 0
        self.daily_limit = 200

    async def complete(self, prompt: str, system: Optional[str] = None, temperature: float = 0.3) -> Optional[str]:
        if not self.available:
            return None
        if self.daily_used >= self.daily_limit:
            logger.warning("OpenRouter daily limit reached")
            return None

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://bunga-trader.local",
            "X-Title": "Bunga Trader",
        }
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload = {"model": MODEL, "messages": messages, "temperature": temperature, "max_tokens": 1024}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(BASE_URL, headers=headers, json=payload, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                    if resp.status == 429:
                        logger.warning("OpenRouter rate limited")
                        return None
                    if resp.status != 200:
                        logger.error(f"OpenRouter API error: {resp.status}")
                        return None
                    data = await resp.json()
                    text = data["choices"][0]["message"]["content"]
                    self.daily_used += 1
                    logger.debug(f"OpenRouter used: {self.daily_used}/{self.daily_limit}")
                    return text
        except Exception as e:
            logger.error(f"OpenRouter request failed: {e}")
            return None

    def get_status(self) -> Dict:
        return {
            "name": self.name,
            "available": self.available,
            "daily_used": self.daily_used,
            "daily_limit": self.daily_limit,
            "remaining": self.daily_limit - self.daily_used,
        }
