from celery import Celery
import time 

celery_app = Celery('neurolibre_celery_tasks', backend='redis://localhost:6379/1', broker='redis://localhost:6379/0')

celery_app.conf.update(
    task_track_started=True
)

@celery_app.task
def rsync():
    time.sleep(30)
    return "I woke up."
    # import subprocess
    # subprocess.call(['rsync', '-avz', source, destination])