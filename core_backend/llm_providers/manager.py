"""LLM Manager - Auto-fallback between free providers"""
import asyncio
from typing import Optional, Dict, List
from .google_provider import GoogleProvider
from .groq_provider import GroqProvider
from .openrouter_provider import OpenRouterProvider
from ..logger import setup_logger

logger = setup_logger("LLMManager")

class LLMManager:
    def __init__(self):
        self.providers = [GoogleProvider(), GroqProvider(), OpenRouterProvider()]
        self._lock = asyncio.Lock()
        logger.info(f"LLM Manager initialized with {len(self.providers)} providers")

    async def complete(self, prompt: str, system: Optional[str] = None, temperature: float = 0.3) -> Optional[str]:
        async with self._lock:
            for provider in self.providers:
                if not provider.available:
                    continue
                logger.debug(f"Trying provider: {provider.name}")
                result = await provider.complete(prompt, system, temperature)
                if result is not None:
                    logger.info(f"Success with {provider.name}")
                    return result
                logger.debug(f"Provider {provider.name} failed, trying next...")
            logger.error("All LLM providers exhausted!")
            return None

    async def complete_with_retry(self, prompt: str, system: Optional[str] = None, 
                                   temperature: float = 0.3, max_retries: int = 2) -> Optional[str]:
        for attempt in range(max_retries + 1):
            result = await self.complete(prompt, system, temperature)
            if result is not None:
                return result
            if attempt < max_retries:
                logger.info(f"Retry attempt {attempt + 1}/{max_retries}")
                await asyncio.sleep(2 ** attempt)
        return None

    def get_status(self) -> List[Dict]:
        return [p.get_status() for p in self.providers]

    def get_best_available(self) -> Optional[str]:
        for p in self.providers:
            if p.available and p.daily_used < p.daily_limit:
                return p.name
        return None

llm_manager = LLMManager()
