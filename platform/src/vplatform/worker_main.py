"""worker 进程入口。

分级轮询：交互 lane 1 个（200ms，人点了按钮要秒回）+ 后台 lane N 个（2s 起退避）。
"""
from __future__ import annotations

import asyncio
import logging
import os

import signal

from vplatform.bootstrap import install
from vplatform.core.db import init_engine
from vplatform.orchestration.handlers import registry
from vplatform.orchestration.worker import Worker, run_pool


async def _serve(bg: int) -> None:
    workers = [Worker(lane="interactive", reg=registry)]
    workers += [Worker(lane="background", reg=registry) for _ in range(bg)]

    loop = asyncio.get_running_loop()
    stopping = asyncio.Event()

    def _shutdown() -> None:
        # **优雅关闭**：不加这个，docker stop 会直接杀进程，在跑的 job 停在
        # running 状态要等 job_lock_timeout_s（900s）才被别人接管，
        # 期间工位和容器全部泄漏。
        logging.getLogger(__name__).info("收到停止信号，等当前 job 跑完…")
        for w in workers:
            w.stop()
        stopping.set()

    # 回收器：僵尸工位 / jobs 归档 / 过期端口租约。
    # 之前这三件事只在注释里存在，worker 崩一次就永久泄漏工位。
    from vplatform.bootstrap import get_factory
    from vplatform.orchestration.reaper import run_reaper

    def _provider():
        fac = get_factory()
        caps = next(iter(fac._cache.values()), None)
        return caps.workspace if caps else None

    reaper = asyncio.create_task(run_reaper(provider_factory=_provider))

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _shutdown)
        except NotImplementedError:      # Windows
            pass

    try:
        await asyncio.gather(*(w.run_forever() for w in workers))
    finally:
        reaper.cancel()


def main() -> None:
    logging.basicConfig(level=os.environ.get("VP_LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    init_engine(create_all=True)
    install()                                    # ← 装配根。没有它 worker 是空转的
    bg = int(os.environ.get("VP_BACKGROUND_WORKERS", "2"))
    asyncio.run(_serve(bg))


if __name__ == "__main__":
    main()
