import logging
import os
import time
import schedule
import threading
from web import app as flask_app

# from tuya_connector import TuyaOpenAPI, TUYA_LOGGER

from balboa import balboa
from elpris import elpris
from ngenic import ngenic
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

    if 'WEB_UI_PORT' in os.environ:
        run_flask()
    
    schedule.every(5).minutes.do(aqualink)
    schedule.every(5).minutes.do(ngenic)
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


def run_flask():
    port = int(os.environ.get('WEB_UI_PORT', 8080))
    def run():
        flask_app.run(host='0.0.0.0', port=port)
    
    logging.info("Web UI port is set, starting Flask server on port {port}...")
    flask_thread = threading.Thread(target=run, daemon=True)
    flask_thread.start()


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
