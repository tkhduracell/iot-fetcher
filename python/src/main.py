import logging
import os
import sys
import time
import schedule

from balboa import balboa
from elpris import elpris
from ngenic import ngenic
from sigenergy import sigenergy
from aqualink import aqualink
from airquality import airquality
from aquatemp import aquatemp

logging.basicConfig(level=logging.INFO,
                    format='%(levelname)s %(message)s')


if os.environ.get('PYDEBUGGER', None):
    import debugpy
    debugpy.listen(("0.0.0.0", 5678))
    debugpy.wait_for_client()
    debugpy.breakpoint()


def main():
    if len(sys.argv) > 1 and sys.argv[1] in ['balboa', 'elpris', 'ngenic', 'sigenergy', 'aqualink', 'aquatemp', 'airquality']:
        module_name = sys.argv[1]
        logging.info(f"Running module: {module_name}")
        for m in [balboa, elpris, ngenic, sigenergy, aqualink, aquatemp, airquality]:
            if m.__name__ == module_name:
                logging.info(f"Executing {module_name} module...")
                m()
        return

    logging.info("Starting the scheduler...")
    schedule.every(5).minutes.do(aqualink)
    schedule.every(5).minutes.do(ngenic)
    schedule.every(1).minutes.do(sigenergy)
    schedule.every(5).minutes.do(balboa)
    schedule.every(5).minutes.do(aquatemp)

    schedule.every(6).hours.at(':05').do(elpris)
    schedule.every(1).hours.at(':05').do(airquality)

    logging.info("Starting the scheduler...")
    schedule.run_all(delay_seconds=10)

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
