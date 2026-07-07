"""Google AI Studio Provider - Primary (1,500 req/day free)"""
import asyncio
from typing import Optional, Dict
import aiohttp
from ..config import CONFIG
from ..logger import setup_logger

logger = setup_logger("GoogleLLM")

BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
MODEL = "gemini-2.0-flash"

class GoogleProvider:
    def __init__(self):
        self.api_key = CONFIG.google_api_key
        self.available = bool(self.api_key)
        self.name = "google"
        self.daily_used = 0
        self.daily_limit = 1500

    async def complete(self, prompt: str, system: Optional[str] = None, temperature: float = 0.3) -> Optional[str]:
        if not self.available:
            return None
        if self.daily_used >= self.daily_limit:
            logger.warning("Google daily limit reached")
            return None

        url = f"{BASE_URL}/models/{MODEL}:generateContent?key={self.api_key}"
        contents = []
        if system:
            contents.append({"role": "user", "parts": [{"text": f"System: {system}"}]})
        contents.append({"role": "user", "parts": [{"text": prompt}]})

        payload = {
            "contents": contents,
            "generationConfig": {"temperature": temperature, "maxOutputTokens": 1024}
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status == 429:
                        logger.warning("Google rate limited")
                        return None
                    if resp.status != 200:
                        logger.error(f"Google API error: {resp.status}")
                        return None
                    data = await resp.json()
                    text = data["candidates"][0]["content"]["parts"][0]["text"]
                    self.daily_used += 1
                    logger.debug(f"Google used: {self.daily_used}/{self.daily_limit}")
                    return text
        except Exception as e:
            logger.error(f"Google request failed: {e}")
            return None

    def get_status(self) -> Dict:
        return {
            "name": self.name,
            "available": self.available,
            "daily_used": self.daily_used,
            "daily_limit": self.daily_limit,
            "remaining": self.daily_limit - self.daily_used,
        }
