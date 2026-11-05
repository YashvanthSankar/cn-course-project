"""Convergence under concurrency and failures.

Six bots edit the same board at once with random ops (adds, moves, recolours,
deletes, restores, batches) while randomly losing their connection, editing
offline and resuming. At the end every replica must hash to exactly the
server's board: same ops, same order, same deterministic apply.

The ``tiny-history`` variant shrinks the server's op history so most resumes
cannot be served from the log and are healed with a full SNAPSHOT instead.
"""

from __future__ import annotations

import asyncio
import random

import pytest

from lanboard.common import config as C
from lanboard.common.messages import Err
from lanboard.server import Server
from lanboard.tools.bot import BotClient, random_op

N_BOTS = 6
DURATION = 3.0          # seconds of concurrent editing
RATE = 40               # ops/s per bot (well under OPS_PER_SEC_LIMIT)


@pytest.mark.parametrize("history", [C.HISTORY_MAX_OPS, 8], ids=["log-resume", "tiny-history"])
async def test_random_concurrent_edits_converge(tmp_path, monkeypatch, history):
    monkeypatch.setattr(C, "HISTORY_MAX_OPS", history)
    srv = Server("127.0.0.1", 0, str(tmp_path / "data"), discovery=False)
    await srv.start()
    bots: list[BotClient] = []
    try:
        for i in range(N_BOTS):
            b = BotClient("127.0.0.1", srv.port, f"bot{i}", "CONV", create=(i == 0))
            await b.connect()
            bots.append(b)
        rng = random.Random(1234 + history)
        loop = asyncio.get_running_loop()
        stop_at = loop.time() + DURATION
        drops = 0

        async def drive(bot: BotClient) -> None:
            nonlocal drops
            while loop.time() < stop_at:
                if rng.random() < 0.02:
                    # connection loss: keep editing offline for a moment, then resume
                    drops += 1
                    bot.drop()
                    for _ in range(rng.randint(0, 3)):
                        bot.submit(random_op(bot, rng))
                    await asyncio.sleep(rng.uniform(0.0, 0.15))
                    await bot.connect()
                    continue
                bot.submit(random_op(bot, rng))
                await asyncio.sleep(rng.uniform(0.5, 1.5) / RATE)

        await asyncio.gather(*(drive(b) for b in bots))
        for b in bots:
            await b.settle(timeout=15)
        room = srv.rooms["CONV"]
        for b in bots:
            await b.wait_until(lambda b=b: b.board.seq == room.seq, timeout=10)

        assert drops > 0 and room.seq > N_BOTS * 20
        server_hash = room.board.hash()
        for b in bots:
            assert b.board.hash() == server_hash, f"{b.username} diverged"
            assert b.hash_mismatches == 0
            assert not [r for r in b.rejects if r["code"] == Err.INVALID_OP]
        assert sum(b.hash_checks for b in bots) > 0          # HASH checkpoints were verified
        if history == 8:
            assert sum(b.snapshots for b in bots) > N_BOTS   # resumes healed via SNAPSHOT
    finally:
        for b in bots:
            await b.close()
        await srv.stop()
