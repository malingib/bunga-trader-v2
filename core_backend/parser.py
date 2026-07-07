"""Bunga Trader - Signal Parser"""
import re
from typing import Optional, Dict, List, Tuple
from .database import get_db
from .models import RawSignal, ParsedSignal, SignalStatus
from .symbols import is_supported_symbol, normalize_signal_symbol
from .logger import setup_logger

logger = setup_logger("Parser")

PATTERN_MULTI_TP = re.compile(
    r'(BUY|SELL)(?:\s+LIMIT|\s+STOP)?\s+((?:GOLD)|[A-Z]{6,10})\s+(\d+\.?\d*)\s+SL\s+(\d+\.?\d*)'
    r'(?:\s+TP1?\s+(\d+\.?\d*))?(?:\s+TP2?\s+(\d+\.?\d*))?(?:\s+TP3?\s+(\d+\.?\d*))?',
    re.IGNORECASE
)
PATTERN_LABELED = re.compile(
    r'(BUY|SELL)(?:\s+LIMIT|\s+STOP)?\s+((?:GOLD)|[A-Z]{6,10}).*?Entry[:\s]+(\d+\.?\d*).*?SL[:\s]+(\d+\.?\d*).*?TP[:\s]+(\d+\.?\d*)',
    re.IGNORECASE | re.DOTALL
)
PATTERN_AT_COLON = re.compile(
    r'(BUY|SELL)(?:\s+LIMIT|\s+STOP)?\s+((?:GOLD)|[A-Z]{6,10})\s*@?\s*(\d+\.?\d*)\s*SL:?\s*(\d+\.?\d*)\s*TP:?\s*(\d+\.?\d*)',
    re.IGNORECASE
)
PATTERN_STANDARD = re.compile(
    r'(BUY|SELL)(?:\s+LIMIT|\s+STOP)?\s+((?:GOLD)|[A-Z]{6,10})\s+(\d+\.?\d*)\s+SL\s+(\d+\.?\d*)\s+TP\s+(\d+\.?\d*)',
    re.IGNORECASE
)
PATTERN_NO_ENTRY = re.compile(
    r'(BUY|SELL)(?:\s+LIMIT|\s+STOP)?\s+((?:GOLD)|[A-Z]{6,10})\s+SL\s+(\d+\.?\d*)\s+TP\s+(\d+\.?\d*)',
    re.IGNORECASE
)

ALL_PATTERNS: List[Tuple[re.Pattern, str]] = [
    (PATTERN_MULTI_TP, "multi_tp"),
    (PATTERN_LABELED, "labeled"),
    (PATTERN_AT_COLON, "at_colon"),
    (PATTERN_STANDARD, "standard"),
    (PATTERN_NO_ENTRY, "no_entry"),
]

def clean_text(text: str) -> str:
    if not text:
        return ""
    text = text.encode("ascii", "ignore").decode("ascii")
    text = " ".join(text.split())
    text = text.upper()
    text = text.replace("STOP LOSS", "SL")
    text = text.replace("STOP-LOSS", "SL")
    text = text.replace("TAKE PROFIT", "TP")
    text = text.replace("TAKE-PROFIT", "TP")
    text = text.replace("ENTRY PRICE", "ENTRY")
    text = text.replace("ENTRY POINT", "ENTRY")
    return text

def extract_action(cleaned: str) -> str:
    if "BUY LIMIT" in cleaned:
        return "BUY_LIMIT"
    elif "SELL LIMIT" in cleaned:
        return "SELL_LIMIT"
    elif "BUY STOP" in cleaned:
        return "BUY_STOP"
    elif "SELL STOP" in cleaned:
        return "SELL_STOP"
    elif "BUY" in cleaned:
        return "BUY"
    elif "SELL" in cleaned:
        return "SELL"
    return ""

def parse_signal_text(text: str) -> Optional[Dict]:
    cleaned = clean_text(text)
    if not cleaned:
        return None
    action = extract_action(cleaned)
    if not action:
        return None
    for pattern, pattern_name in ALL_PATTERNS:
        match = pattern.search(cleaned)
        if match:
            groups = match.groups()
            result = {"action": action, "pattern": pattern_name}
            if pattern_name == "multi_tp":
                result["symbol"] = groups[1]
                result["entry"] = float(groups[2]) if groups[2] else None
                result["sl"] = float(groups[3]) if groups[3] else None
                result["tp"] = float(groups[4]) if groups[4] else None
                result["tp2"] = float(groups[5]) if len(groups) > 5 and groups[5] else None
                result["tp3"] = float(groups[6]) if len(groups) > 6 and groups[6] else None
            elif pattern_name == "labeled":
                result["symbol"] = groups[1]
                result["entry"] = float(groups[2]) if groups[2] else None
                result["sl"] = float(groups[3]) if groups[3] else None
                result["tp"] = float(groups[4]) if groups[4] else None
            elif pattern_name in ("at_colon", "standard"):
                result["symbol"] = groups[1]
                result["entry"] = float(groups[2]) if groups[2] else None
                result["sl"] = float(groups[3]) if groups[3] else None
                result["tp"] = float(groups[4]) if groups[4] else None
            elif pattern_name == "no_entry":
                result["symbol"] = groups[1]
                result["entry"] = None
                result["sl"] = float(groups[2]) if groups[2] else None
                result["tp"] = float(groups[3]) if groups[3] else None
            symbol = normalize_signal_symbol(result.get("symbol", ""))
            if not is_supported_symbol(symbol):
                continue
            result["symbol"] = symbol
            for key in ["entry", "sl", "tp", "tp2", "tp3"]:
                if result.get(key) is not None and result[key] <= 0:
                    result[key] = None
            return result
    return None


def _store_parsed_signal(db, raw: RawSignal, parsed: Dict) -> bool:
    existing = (
        db.query(ParsedSignal)
        .filter(ParsedSignal.raw_signal_id == raw.id)
        .first()
    )
    if existing:
        raw.processed = 1
        return False

    ps = ParsedSignal(
        raw_signal_id=raw.id,
        action=parsed["action"],
        symbol=parsed["symbol"],
        entry_price=parsed.get("entry"),
        sl=parsed.get("sl"),
        tp=parsed.get("tp"),
        tp2=parsed.get("tp2"),
        tp3=parsed.get("tp3"),
        raw_text=raw.text,
        status=SignalStatus.PENDING.value,
    )
    db.add(ps)
    raw.processed = 1
    logger.info(f"Parsed {raw.id}: {parsed['action']} {parsed['symbol']}")
    return True


def process_raw_signal(raw_signal_id: int) -> bool:
    """Parse and persist one raw signal by id."""
    with get_db() as db:
        raw = db.query(RawSignal).filter(RawSignal.id == raw_signal_id).first()
        if not raw:
            return False
        parsed = parse_signal_text(raw.text)
        if not parsed:
            raw.processed = -1
            return False
        return _store_parsed_signal(db, raw, parsed)

def process_unparsed_signals() -> int:
    parsed_count = 0
    with get_db() as db:
        try:
            raw_signals = (
                db.query(RawSignal)
                .filter(RawSignal.processed == 0)
                .order_by(RawSignal.timestamp)
                .limit(100)
                .all()
            )
            for raw in raw_signals:
                try:
                    parsed = parse_signal_text(raw.text)
                    if parsed:
                        created = _store_parsed_signal(db, raw, parsed)
                        if created:
                            parsed_count += 1
                    else:
                        raw.processed = -1
                except Exception as e:
                    logger.error(f"Error parsing message {raw.id}: {e}")
                    raw.processed = -1
            db.commit()
        except Exception as e:
            logger.error(f"Database error: {e}")
            db.rollback()
            raise
    return parsed_count
