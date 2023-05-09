from celery import Celery
import time
import os 
import subprocess
from celery import states
import pytz
import datetime
from github_client import *
from github import Github
from dotenv import load_dotenv

load_dotenv()

celery_app = Celery('neurolibre_celery_tasks', backend='redis://localhost:6379/1', broker='redis://localhost:6379/0')

celery_app.conf.update(
    task_track_started=True
)

def get_time():
    tz = pytz.timezone('US/Pacific')
    now = datetime.datetime.now(tz)
    cur_time = now.strftime('%Y-%m-%d %H:%M:%S %Z')
    return cur_time

@celery_app.task(bind=True)
def sleep_task(self, seconds):
    for i in range(seconds):
        time.sleep(1)
        self.update_state(state='PROGRESS', meta={'remaining': seconds - i - 1})
    return 'done sleeping for {} seconds'.format(seconds)

@celery_app.task(bind=True)
def rsync_data(self, comment_id, issue_id, project_name, reviewRepository):
    task_title = "DATA TRANSFER (Preview --> Preprint)"
    GH_BOT=os.getenv('GH_BOT')
    github_client = Github(GH_BOT)
    task_id = self.request.id
    remote_path = os.path.join("neurolibre-preview:", "DATA", project_name)
    try:
        f = open("/DATA/data_synclog.txt", "a")
        f.write(remote_path)
        f.close()
        now = get_time()
        self.update_state(state=states.STARTED, meta={'message': f"Transfer started {now}"})
        gh_template_respond(github_client,"started",task_title,reviewRepository,issue_id,task_id,comment_id, "")
        subprocess.check_call(["/usr/bin/rsync", "-avR", remote_path, "/"])
    except subprocess.CalledProcessError as e:
        gh_template_respond(github_client,"failure",task_title,reviewRepository,issue_id,task_id,comment_id, f"{e.output}")
        self.update_state(state=states.FAILURE, meta={'message': e.output})
    # final check
    if len(os.listdir(os.path.join("/DATA", project_name))) == 0:
        self.update_state(state=states.FAILURE, meta={'message': f"Data sync was not successful for {project_name}"})
        gh_template_respond(github_client,"failure",task_title,reviewRepository,issue_id,task_id,comment_id, f"Data sync was not successful for {project_name}")
    else:
        gh_template_respond(github_client,"success",task_title,reviewRepository,issue_id,task_id,comment_id, "")
        self.update_state(state=states.SUCCESS, meta={'message': f"Data sync has been completed for {project_name}"})