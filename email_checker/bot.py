import asyncio
import datetime
import io
import logging
import os
from pathlib import Path
from zoneinfo import ZoneInfo

import discord

from . import DELETE, HOLD, KEEP, classify, db, jobs, report
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
        boxes = []
        try:
            for a in ("naver", "gmail"):
                boxes.append(Mailbox(a, os.environ[f"{a.upper()}_USER"], os.environ[f"{a.upper()}_APP_PASSWORD"]))
        except Exception:
            for b in boxes:
                b.close()
            raise
        return boxes

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
        await ch.send(f"📬 검토할 메일 {sum(counts.values())}통 (삭제 {counts[DELETE]} · 확인 {counts[KEEP]} · "
                      f"보류 {counts[HOLD]})\n"
                      "첨부 CSV의 '분류' 칸을 삭제/확인/보류로 고쳐 이 채널에 다시 올려 주세요. "
                      "고치지 않은 줄은 맞은 것으로, 보류로 둔 줄은 다음 보고서에 다시 실려요.",
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
            return
        for att in message.attachments:
            if not att.filename.lower().endswith(".csv"):
                continue
            data = await att.read()
            try:
                async with lock:
                    text, _ = await asyncio.to_thread(jobs.review, conn, data)
            except report.CsvError as e:
                await message.reply(f"⚠️ {e}")
                continue
            except Exception as e:
                log.exception("review failed")
                await message.reply(f"⚠️ 처리하지 못했어요: {e}")
                continue
            await message.reply(text)

    client.run(os.environ["DISCORD_TOKEN"])
