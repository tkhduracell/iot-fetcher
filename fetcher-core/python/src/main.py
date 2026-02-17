import concurrent.futures
import logging
import os
import sys
import time
import schedule

from balboa import balboa
# from balboa import balboa_control  # Disabled SPA control
from elpris import elpris
from ngenic import ngenic
from sigenergy import sigenergy
from aqualink import aqualink
from airquality import airquality
from aquatemp import aquatemp
from tapo import tapo
from sonos import sonos
from backup_influx import backup_influx
from eufy import eufy, eufy_snapshot

logging.basicConfig(level=logging.INFO,
                    format='%(levelname)s %(message)s')


def with_timeout(func, timeout_seconds=120):
    """Wrap a scheduled job with a timeout to prevent the scheduler from getting stuck."""
    def wrapper():
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(func)
            try:
                future.result(timeout=timeout_seconds)
            except concurrent.futures.TimeoutError:
                logging.error("[scheduler] %s timed out after %ds", func.__name__, timeout_seconds)
            except Exception as e:
                logging.error("[scheduler] %s failed: %s", func.__name__, e)
    wrapper.__name__ = func.__name__
    return wrapper


if os.environ.get('PYDEBUGGER', None):
    import debugpy
    debugpy.listen(("0.0.0.0", 5678))
    debugpy.wait_for_client()
    debugpy.breakpoint()


def main():
    if len(sys.argv) > 1 and sys.argv[1] in ['balboa', 'elpris', 'ngenic', 'sigenergy', 'aqualink', 'aquatemp', 'airquality', 'tapo', 'sonos', 'backup_influx', 'eufy', 'eufy_snapshot']:
        module_name = sys.argv[1]
        logging.info(f"Running module: {module_name}")
        for m in [balboa, elpris, ngenic, sigenergy, aqualink, aquatemp, airquality, tapo, sonos, backup_influx, eufy, eufy_snapshot]:
            if m.__name__ == module_name:
                logging.info(f"Executing {module_name} module...")
                m()
        return

    logging.info("Starting the scheduler...")
    schedule.every(1).minutes.do(with_timeout(aqualink))
    schedule.every(5).minutes.do(with_timeout(ngenic))
    schedule.every(1).minutes.do(with_timeout(sigenergy))
    schedule.every(5).minutes.do(with_timeout(balboa))
    schedule.every(5).minutes.do(with_timeout(aquatemp))
    schedule.every(5).minutes.do(with_timeout(tapo))
    schedule.every(5).minutes.do(with_timeout(eufy))
    schedule.every(1).minutes.do(with_timeout(sonos))

    schedule.every(6).hours.at(':05').do(with_timeout(elpris))
    schedule.every(1).hours.at(':05').do(with_timeout(airquality))
    # schedule.every(1).hours.at(':10').do(with_timeout(balboa_control))  # Disabled SPA module
    schedule.every(3).hours.at(':15').do(with_timeout(eufy_snapshot))

    logging.info("Starting the scheduler, running all...")
    schedule.run_all(delay_seconds=10)

    # Avoid this from running every startup
    schedule.every(12).hours.at(':10').do(with_timeout(backup_influx))

    while 1:
        schedule.run_pending()
        try:
            time.sleep(1)
        except KeyboardInterrupt:
            break
        except Exception as e:
            logging.info(f"An error occurred: {e}")


if __name__ == '__main__':
    print(r"""
$$\            $$\            $$$$$$\            $$\               $$\                           
\__|           $$ |          $$  __$$\           $$ |              $$ |                          
$$\  $$$$$$\ $$$$$$\         $$ /  \__|$$$$$$\ $$$$$$\    $$$$$$$\ $$$$$$$\   $$$$$$\   $$$$$$\  
$$ |$$  __$$\\_$$  _|$$$$$$\ $$$$\    $$  __$$\\_$$  _|  $$  _____|$$  __$$\ $$  __$$\ $$  __$$\ 
$$ |$$ /  $$ | $$ |  \______|$$  _|   $$$$$$$$ | $$ |    $$ /      $$ |  $$ |$$$$$$$$ |$$ |  \__|
$$ |$$ |  $$ | $$ |$$\       $$ |     $$   ____| $$ |$$\ $$ |      $$ |  $$ |$$   ____|$$ |      
$$ |\$$$$$$  | \$$$$  |      $$ |     \$$$$$$$\  \$$$$  |\$$$$$$$\ $$ |  $$ |\$$$$$$$\ $$ |      
\__| \______/   \____/       \__|      \_______|  \____/  \_______|\__|  \__| \_______|\__|      
                                                                                                           
                                                     
""", flush=True)
    main()
