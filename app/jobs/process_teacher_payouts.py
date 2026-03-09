# app/jobs/process_teacher_payouts.py
# Monthly payout settlement job.

import logging

from app.db.session import SessionLocal
from app.services.payout_service import create_monthly_settlement_rows, trigger_pending_payouts


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("tamgam.payout-job")


def run() -> None:
    db = SessionLocal()
    try:
        created = create_monthly_settlement_rows(db)
        db.commit()
        logger.info("teacher payout settlement rows created=%s", len(created))

        processed = trigger_pending_payouts(db)
        db.commit()
        logger.info("teacher payouts triggered count=%s", len(processed))
    except Exception:
        db.rollback()
        logger.exception("teacher payout job failed")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    run()
