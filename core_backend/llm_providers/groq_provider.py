"""Groq Provider - Fallback 1 (30 RPM / ~1,000 req/day free)"""
import asyncio
from typing import Optional, Dict
import aiohttp
from ..config import CONFIG
from ..logger import setup_logger

logger = setup_logger("GroqLLM")

BASE_URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL = "llama-3.3-70b-versatile"

class GroqProvider:
    def __init__(self):
        self.api_key = CONFIG.groq_api_key
        self.available = bool(self.api_key)
        self.name = "groq"
        self.daily_used = 0
        self.daily_limit = 1000
        self.minute_used = 0
        self.minute_limit = 30
        self.last_reset = asyncio.get_event_loop().time()

    def _check_minute_reset(self):
        now = asyncio.get_event_loop().time()
        if now - self.last_reset >= 60:
            self.minute_used = 0
            self.last_reset = now

    async def complete(self, prompt: str, system: Optional[str] = None, temperature: float = 0.3) -> Optional[str]:
        if not self.available:
            return None
        if self.daily_used >= self.daily_limit:
            logger.warning("Groq daily limit reached")
            return None

        self._check_minute_reset()
        if self.minute_used >= self.minute_limit:
            logger.warning("Groq minute limit reached")
            return None

        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload = {"model": MODEL, "messages": messages, "temperature": temperature, "max_tokens": 1024}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(BASE_URL, headers=headers, json=payload, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status == 429:
                        logger.warning("Groq rate limited")
                        return None
                    if resp.status != 200:
                        logger.error(f"Groq API error: {resp.status}")
                        return None
                    data = await resp.json()
                    text = data["choices"][0]["message"]["content"]
                    self.daily_used += 1
                    self.minute_used += 1
                    logger.debug(f"Groq used: {self.daily_used}/{self.daily_limit} (min: {self.minute_used})")
                    return text
        except Exception as e:
            logger.error(f"Groq request failed: {e}")
            return None

    def get_status(self) -> Dict:
        self._check_minute_reset()
        return {
            "name": self.name,
            "available": self.available,
            "daily_used": self.daily_used,
            "daily_limit": self.daily_limit,
            "remaining": self.daily_limit - self.daily_used,
            "minute_used": self.minute_used,
            "minute_limit": self.minute_limit,
        }
