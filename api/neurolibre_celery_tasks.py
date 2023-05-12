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
import logging



logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')

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
        logging.info("Calling subprocess")
        #out = subprocess.check_output(["/usr/bin/rsync", "-avR", remote_path, "/"], cwd="/usr/bin")
        process = subprocess.Popen(["/usr/bin/rsync", "-avR", remote_path, "/"], stdout=subprocess.PIPE,stderr=subprocess.STDOUT) 
        output = process.communicate()[0]
        ret = process.wait()
        logging.info(output)
    except subprocess.CalledProcessError as e:
        logging.info("Subprocess exception")
        gh_template_respond(github_client,"failure",task_title,reviewRepository,issue_id,task_id,comment_id, f"{e.output}")
        self.update_state(state=states.FAILURE, meta={'message': e.output})
    # final check
    if os.path.exists(os.path.join("/DATA", project_name)):
        if len(os.listdir(os.path.join("/DATA", project_name))) == 0:
            logging.info("Listdir exception")
            self.update_state(state=states.FAILURE, meta={'message': f"Directory exists but empty {project_name}"})
            gh_template_respond(github_client,"failure",task_title,reviewRepository,issue_id,task_id,comment_id, f"Directory exists but empty: {project_name}")
        else:
            gh_template_respond(github_client,"success",task_title,reviewRepository,issue_id,task_id,comment_id, "")
            self.update_state(state=states.SUCCESS, meta={'message': f"Data sync has been completed for {project_name}"})
    else:
        logging.info("No dir exemption")
        self.update_state(state=states.FAILURE, meta={'message': f"Directory does not exist {project_name}"})
        gh_template_respond(github_client,"failure",task_title,reviewRepository,issue_id,task_id,comment_id, f"Directory does not exist: {project_name}")