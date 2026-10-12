import asyncio
import datetime
import io
import logging
import os
import subprocess
from pathlib import Path
from zoneinfo import ZoneInfo

import discord

from . import DELETE, HOLD, KEEP, classify, db, jobs, report, train
from .mailbox import Mailbox

KST = ZoneInfo("Asia/Seoul")
log = logging.getLogger(__name__)


def run() -> None:
    logging.basicConfig(level=logging.INFO)
    data_dir = Path(os.environ.get("DATA_DIR", "data"))
    data_dir.mkdir(exist_ok=True)
    channel_id = int(os.environ["DISCORD_CHANNEL_ID"])
    conn = db.connect(data_dir / "email-checker.db")
    models_dir = data_dir / "models"
    model = {"classifier": None}
    lock = asyncio.Lock()          # DB와 모델을 쓰는 작업을 한 번에 하나만
    retrain_lock = asyncio.Lock()  # 재학습은 한 번에 하나만, 밀린 요청은 차례로
    background = set()

    def load_classifier():
        current = models_dir / "current"
        return classify.Classifier(current if current.exists() else None)

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

    async def retrain(ch):
        async with retrain_lock:
            work = models_dir / "work"
            try:
                async with lock:
                    n_train, n_eval = await asyncio.to_thread(train.export, conn, work)
                if n_train < train.MIN_TRAIN or n_eval < train.MIN_EVAL:
                    await ch.send(f"학습자료가 아직 부족해서 재학습을 건너뛰었어요 "
                                  f"(학습 {n_train}/{train.MIN_TRAIN}통, 시험 {n_eval}/{train.MIN_EVAL}통).")
                    return
                await ch.send(f"🧠 재학습을 시작했어요 (학습 {n_train}통, 시험 {n_eval}통). 끝나면 알려 드릴게요.")
                # 몇 시간 걸리는 학습은 lock 밖에서 돌려 보고서와 검토를 막지 않는다.
                candidate = await asyncio.to_thread(train.run_laya, work)
                # 두 모델을 같은 방식(정답별 맞힌 비율의 평균)으로 직접 잰다.
                fresh = await asyncio.to_thread(classify.Classifier, candidate)
                new = await asyncio.to_thread(fresh.accuracy, work / "eval.jsonl")
                async with lock:
                    old = await asyncio.to_thread(model["classifier"].accuracy, work / "eval.jsonl")
                    if new >= old:
                        train.promote(models_dir, candidate)
                        model["classifier"] = await asyncio.to_thread(load_classifier)
                        db.reset_predictions(conn)
            except subprocess.CalledProcessError as e:
                log.error("laya-train failed: %s", e.stderr)
                await ch.send(f"⚠️ 재학습이 실패했어요:\n```{(e.stderr or '')[-1500:]}```")
                return
            except Exception as e:
                log.exception("retrain failed")
                await ch.send(f"⚠️ 재학습이 실패했어요: {e}")
                return
        verdict = "새 모델로 교체했어요. 다음 보고서는 남은 메일을 새 모델로 다시 분류해요" if new >= old else "기존 모델이 더 나아서 교체하지 않았어요"
        await ch.send(f"재학습 끝: 시험 정확도(삭제·확인 각각 맞힌 비율의 평균) 기존 {old:.0%} → 새 모델 {new:.0%}. {verdict}.")

    def schedule_retrain(ch):
        task = asyncio.create_task(retrain(ch))
        background.add(task)
        task.add_done_callback(background.discard)

    @client.event
    async def on_ready():
        if model["classifier"] is None:
            model["classifier"] = await asyncio.to_thread(load_classifier)
        log.info("ready as %s", client.user)

    @client.event
    async def on_message(message):
        if message.author.bot or message.channel.id != channel_id:
            return
        if message.content.strip() == "!보고서":
            await daily_report(message.channel)
            return
        if message.content.strip() == "!재학습":
            if retrain_lock.locked():
                await message.reply("이미 재학습 중이에요. 끝나면 이어서 한 번 더 돌릴게요.")
            schedule_retrain(message.channel)
            return
        for att in message.attachments:
            if not att.filename.lower().endswith(".csv"):
                continue
            data = await att.read()
            try:
                async with lock:
                    text, learned = await asyncio.to_thread(jobs.review, conn, data)
            except report.CsvError as e:
                await message.reply(f"⚠️ {e}")
                continue
            except Exception as e:
                log.exception("review failed")
                await message.reply(f"⚠️ 처리하지 못했어요: {e}")
                continue
            await message.reply(text)
            if learned:
                schedule_retrain(message.channel)

    client.run(os.environ["DISCORD_TOKEN"])
