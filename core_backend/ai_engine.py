"""Bunga Trader - AI Signal Validation Engine"""
import json
import asyncio
from typing import Optional, Tuple, List
from .models import ParsedSignal
from .llm_providers.manager import llm_manager
from .symbols import is_supported_symbol
from .logger import setup_logger

logger = setup_logger("AIEngine")

SUSPICIOUS_PATTERNS = [
    "guaranteed profit", "100% win", "no loss", "sure shot",
    "guaranteed", "risk free", "get rich", "millionaire", "double your",
]

QUALITY_INDICATORS = [
    "sl", "stop loss", "tp", "take profit", "entry", "risk",
    "position size", "lot", "analysis", "support", "resistance",
    "trend", "breakout", "consolidation",
]

LLM_SYSTEM_PROMPT = """You are a forex signal quality analyst. Analyze the trading signal and respond ONLY with a JSON object in this exact format:
{
  "score": 0.0 to 1.0,
  "approved": true/false,
  "reason": "brief explanation",
  "risk_level": "low/medium/high",
  "confidence": 0.0 to 1.0
}

Scoring criteria:
- 0.8-1.0: Clear signal with entry, SL, TP, proper risk management
- 0.5-0.7: Decent signal but missing some details
- 0.3-0.4: Vague or suspicious signal
- 0.0-0.2: Spam, scam, or completely invalid

Reject signals that:
- Promise guaranteed profits
- Have no stop loss
- Are extremely vague
- Use manipulative language
- Have unrealistic targets (e.g., 500 pips on a 10-pip SL)"""


def rule_based_score(signal: ParsedSignal) -> Tuple[float, Optional[str]]:
    """Fast rule-based scoring (no API call needed)."""
    if not signal or not signal.raw_text:
        return 0.0, "Empty signal"

    text_lower = signal.raw_text.lower()
    score = 0.5
    reasons = []

    for pattern in SUSPICIOUS_PATTERNS:
        if pattern in text_lower:
            score -= 0.3
            reasons.append(f"Suspicious: '{pattern}'")

    quality_hits = sum(1 for ind in QUALITY_INDICATORS if ind in text_lower)
    score += min(quality_hits * 0.05, 0.2)

    if signal.action in ("BUY", "SELL", "BUY_LIMIT", "SELL_LIMIT", "BUY_STOP", "SELL_STOP"):
        score += 0.1
    else:
        score -= 0.2
        reasons.append("Unknown action")

    if signal.symbol and is_supported_symbol(signal.symbol):
        score += 0.1
    else:
        score -= 0.1
        reasons.append("Invalid symbol")

    if signal.sl and signal.sl > 0:
        score += 0.1
    else:
        score -= 0.2
        reasons.append("No SL")

    if signal.tp and signal.tp > 0:
        score += 0.05

    if signal.entry_price and signal.entry_price > 0:
        score += 0.05

    if signal.entry_price and signal.sl and signal.tp:
        risk = abs(signal.entry_price - signal.sl)
        reward = abs(signal.tp - signal.entry_price)
        if risk > 0:
            rr = reward / risk
            if rr >= 2:
                score += 0.1
            elif rr < 1:
                score -= 0.1
                reasons.append(f"Poor R:R ({rr:.1f})")

    score = max(0.0, min(1.0, score))
    reason = "; ".join(reasons) if reasons else None
    return score, reason


async def llm_enhanced_score(signal: ParsedSignal) -> Tuple[float, float, Optional[str]]:
    """LLM-powered signal analysis with fallback to rule-based."""
    rule_score, rule_reason = rule_based_score(signal)

    if rule_score >= 0.85:
        logger.info(f"Signal {signal.id}: Rule score {rule_score:.2f}, skipping LLM")
        return rule_score, 0.9, rule_reason or "High quality signal"

    if rule_score <= 0.2:
        logger.info(f"Signal {signal.id}: Rule score {rule_score:.2f}, skipping LLM")
        return rule_score, 0.9, rule_reason or "Low quality signal"

    prompt = f"""Analyze this forex trading signal:

Raw text: {signal.raw_text}
Action: {signal.action}
Symbol: {signal.symbol}
Entry: {signal.entry_price or 'MARKET'}
SL: {signal.sl}
TP: {signal.tp}
TP2: {signal.tp2 or 'N/A'}
TP3: {signal.tp3 or 'N/A'}

Rule-based score: {rule_score:.2f}

Provide your analysis as JSON only."""

    try:
        llm_response = await llm_manager.complete_with_retry(
            prompt=prompt,
            system=LLM_SYSTEM_PROMPT,
            temperature=0.2,
            max_retries=1
        )

        if llm_response:
            try:
                start = llm_response.find('{')
                end = llm_response.rfind('}') + 1
                if start >= 0 and end > start:
                    json_str = llm_response[start:end]
                    analysis = json.loads(json_str)

                    llm_score = float(analysis.get("score", rule_score))
                    llm_confidence = float(analysis.get("confidence", 0.5))
                    llm_reason = analysis.get("reason", "LLM analysis")

                    final_score = (llm_score * 0.6) + (rule_score * 0.4)
                    final_score = max(0.0, min(1.0, final_score))

                    logger.info(f"Signal {signal.id}: LLM={llm_score:.2f}, Rule={rule_score:.2f}, Final={final_score:.2f}")
                    return final_score, llm_confidence, llm_reason
            except (json.JSONDecodeError, ValueError) as e:
                logger.warning(f"Failed to parse LLM response: {e}")
    except Exception as e:
        logger.error(f"LLM analysis failed: {e}")

    logger.info(f"Signal {signal.id}: Using rule-based score {rule_score:.2f}")
    return rule_score, 0.6, rule_reason or "Rule-based fallback"


async def ai_validate_signal(signal: ParsedSignal) -> Tuple[bool, float, Optional[str]]:
    """Main validation function. Returns: (approved, confidence_score, reason)"""
    score, confidence, reason = await llm_enhanced_score(signal)

    if score < 0.3:
        logger.warning(f"AI rejected signal {signal.id}: {reason} (score={score:.2f})")
        return False, score, reason

    logger.info(f"AI approved signal {signal.id} (score={score:.2f}, confidence={confidence:.2f})")
    return True, score, reason


async def ai_batch_validate(signals: List[ParsedSignal]) -> List[Tuple[bool, float, Optional[str]]]:
    """Validate multiple signals in parallel."""
    tasks = [ai_validate_signal(s) for s in signals]
    return await asyncio.gather(*tasks)
