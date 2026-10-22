import logging
import time

from config import get_settings
from db import SessionLocal
from services.job_executor import JobExecutor
from services.study_package_builder import PackageBuildRunner


def run_worker_loop() -> None:
    settings = get_settings()
    packages = PackageBuildRunner(SessionLocal, settings)
    packages.recover_interrupted()
    logging.info("worker loop started")
    while True:
        try:
            package_worked = packages.poll()
            with SessionLocal() as db:
                worked = JobExecutor(db).process_cycle()
            if not worked and not package_worked:
                logging.info("no queued jobs")
        except Exception:  # noqa: BLE001
            logging.exception("worker iteration failed; will retry")
        time.sleep(settings.worker_poll_interval_seconds)
