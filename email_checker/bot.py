import asyncio
import datetime
import io
import logging
import os
from pathlib import Path
from zoneinfo import ZoneInfo

import discord

from . import DELETE, HOLD, KEEP, classify, db, jobs
from .mailbox import Mailbox

KST = ZoneInfo("Asia/Seoul")
log = logging.getLogger(__name__)


def run() -> None:
    logging.basicConfig(level=logging.INFO)
    data_dir = Path(os.environ.get("DATA_DIR", "data"))
    data_dir.mkdir(exist_ok=True)
    channel_id = int(os.environ["DISCORD_CHANNEL_ID"])
    conn = db.connect(data_dir / "email-checker.db")
    model = {"classifier": None}
    lock = asyncio.Lock()  # DB와 모델을 쓰는 작업을 한 번에 하나만

    def open_boxes():
        return [Mailbox("gmail", os.environ["GMAIL_USER"], os.environ["GMAIL_APP_PASSWORD"])]

    intents = discord.Intents.default()
    intents.message_content = True  # 명령과 첨부 파일을 읽으려면 필요하다
    client = discord.Client(intents=intents)

    async def daily_report(ch):
        try:
            async with lock:
                data, counts = await asyncio.to_thread(jobs.daily, conn, open_boxes, model["classifier"])
        except Exception as e:
            log.exception("daily report failed")
            await ch.send(f"⚠️ 보고서를 만들지 못했어요: {e}")
            return
        if not sum(counts.values()):
            await ch.send("검토할 메일이 없어요.")
            return
        name = f"보고서-{datetime.datetime.now(KST):%Y-%m-%d}.csv"
        await ch.send(f"삭제 {counts[DELETE]} · 확인 {counts[KEEP]} · 보류 {counts[HOLD]}",
                      file=discord.File(io.BytesIO(data), filename=name))

    @client.event
    async def on_ready():
        if model["classifier"] is None:
            model["classifier"] = await asyncio.to_thread(classify.Classifier)
        log.info("ready as %s", client.user)

    @client.event
    async def on_message(message):
        if message.author.bot or message.channel.id != channel_id:
            return
        if message.content.strip() == "!보고서":
            await daily_report(message.channel)

    client.run(os.environ["DISCORD_TOKEN"])
