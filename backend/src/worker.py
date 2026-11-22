import logging
import time
from pathlib import Path
from src.services.execution import claim, execute, materialize_due
from src.services.probe import claim_recalc, execute_recalc
from src.utils.wakeup import wait

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)
heartbeat = Path('/tmp/analysis-heartbeat')


def main():
    while True:
        try:
            materialize_due()
            lease = claim()
            heartbeat.touch()
            if lease:
                execute(lease)
                heartbeat.touch()
                continue
            recalc = claim_recalc()
            if recalc:
                execute_recalc(recalc)
                heartbeat.touch()
            else:
                wait()
        except Exception:
            log.exception('Worker cycle failed')
            time.sleep(2)


if __name__ == '__main__':
    main()
