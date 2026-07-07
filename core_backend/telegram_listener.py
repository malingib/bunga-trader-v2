"""Bunga Trader - Telegram Listener"""
import asyncio
from datetime import datetime
from telethon import TelegramClient, events
from .config import CONFIG
from .database import SessionLocal
from .models import RawSignal
from .parser import process_raw_signal
from .logger import setup_logger

logger = setup_logger("TelegramListener")

class SignalListener:
    def __init__(self):
        self.client = TelegramClient('data/bunga_session', CONFIG.tg_api_id, CONFIG.tg_api_hash)
        self.channel_entities = []
        self._running = False

    async def start(self):
        logger.info("Starting Telegram listener...")
        try:
            await self.client.start(phone=CONFIG.tg_phone)
            logger.info("Telegram client authenticated")
        except Exception as e:
            logger.error(f"Telegram authentication failed: {e}")
            raise
        self.channel_entities = []
        for chan in CONFIG.signal_channels:
            try:
                if isinstance(chan, str) and not chan.startswith('-'):
                    entity = await self.client.get_entity(chan)
                else:
                    entity = await self.client.get_entity(int(chan))
                self.channel_entities.append(entity)
                logger.info(f"Listening to: {getattr(entity, 'title', chan)}")
            except Exception as e:
                logger.error(f"Failed to resolve channel {chan}: {e}")
        if not self.channel_entities:
            logger.warning("No valid channels to listen to!")
            return
        @self.client.on(events.NewMessage(chats=self.channel_entities))
        async def new_message_handler(event):
            await self._handle_message(event.message, is_edit=False)
        @self.client.on(events.MessageEdited(chats=self.channel_entities))
        async def edit_handler(event):
            await self._handle_message(event.message, is_edit=True)
        self._running = True
        logger.info("Handlers registered. Listening for messages...")
        try:
            await self.client.run_until_disconnected()
        except Exception as e:
            logger.error(f"Listener error: {e}")
            raise
        finally:
            self._running = False

    async def _handle_message(self, message, is_edit: bool = False):
        if not message.text:
            return
        try:
            chat = message.peer_id
            if hasattr(chat, 'channel_id'):
                channel_id = f"-100{chat.channel_id}"
            else:
                channel_id = str(chat)
            db = SessionLocal()
            try:
                if is_edit:
                    existing = (
                        db.query(RawSignal)
                        .filter(RawSignal.channel_id == channel_id, RawSignal.message_id == message.id)
                        .first()
                    )
                    if existing:
                        existing.text = message.text
                        existing.edit_date = message.edit_date or datetime.utcnow()
                        existing.processed = 0
                        db.commit()
                        logger.info(f"Updated edited message {message.id}")
                        return
                    else:
                        is_edit = False
                if not is_edit:
                    existing = (
                        db.query(RawSignal)
                        .filter(RawSignal.channel_id == channel_id, RawSignal.message_id == message.id)
                        .first()
                    )
                    if existing:
                        logger.debug(f"Duplicate message {message.id}, skipping")
                        return
                    record = RawSignal(
                        channel_id=channel_id,
                        message_id=message.id,
                        sender_id=str(message.sender_id) if message.sender_id else None,
                        text=message.text,
                        timestamp=message.date or datetime.utcnow(),
                        processed=0,
                    )
                    db.add(record)
                    db.commit()
                    preview = message.text[:80].replace(chr(10), " ")
                    logger.info(f"Saved signal: {preview}...")
                    if record.id:
                        await asyncio.to_thread(process_raw_signal, record.id)
            except Exception as e:
                logger.error(f"Database error: {e}")
                db.rollback()
            finally:
                db.close()
        except Exception as e:
            logger.error(f"Error processing message: {e}")

    def stop(self):
        self._running = False
        logger.info("Listener stop requested")

if __name__ == "__main__":
    listener = SignalListener()
    try:
        asyncio.run(listener.start())
    except KeyboardInterrupt:
        logger.info("Stopped by user")
        listener.stop()
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        raise
