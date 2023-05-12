from celery import Celery
import time
import os 
import subprocess
from celery import states
import pytz
import datetime
from github_client import *
from common import *
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
        # TODO: improve this, subpar logging.
        f = open("/DATA/data_synclog.txt", "a")
        f.write(remote_path)
        f.close()
        now = get_time()
        self.update_state(state=states.STARTED, meta={'message': f"Transfer started {now}"})
        gh_template_respond(github_client,"started",task_title,reviewRepository,issue_id,task_id,comment_id, "")
        #logging.info("Calling subprocess")
        process = subprocess.Popen(["/usr/bin/rsync", "-avR", remote_path, "/"], stdout=subprocess.PIPE,stderr=subprocess.STDOUT) 
        output = process.communicate()[0]
        ret = process.wait()
        #logging.info(output)
    except subprocess.CalledProcessError as e:
        #logging.info("Subprocess exception")
        gh_template_respond(github_client,"failure",task_title,reviewRepository,issue_id,task_id,comment_id, f"{e.output}")
        self.update_state(state=states.FAILURE, meta={'message': e.output})
    # final check
    if os.path.exists(os.path.join("/DATA", project_name)):
        if len(os.listdir(os.path.join("/DATA", project_name))) == 0:
            #logging.info("Listdir exception")
            self.update_state(state=states.FAILURE, meta={'message': f"Directory exists but empty {project_name}"})
            gh_template_respond(github_client,"failure",task_title,reviewRepository,issue_id,task_id,comment_id, f"Directory exists but empty: {project_name}")
        else:
            gh_template_respond(github_client,"success",task_title,reviewRepository,issue_id,task_id,comment_id, output)
            self.update_state(state=states.SUCCESS, meta={'message': f"Data sync has been completed for {project_name}"})
    else:
        #logging.info("No dir exemption")
        self.update_state(state=states.FAILURE, meta={'message': f"Directory does not exist {project_name}"})
        gh_template_respond(github_client,"failure",task_title,reviewRepository,issue_id,task_id,comment_id, f"Directory does not exist: {project_name}")

@celery_app.task(bind=True)
def rsync_book(self, repo_url, commit_hash, comment_id, issue_id, reviewRepository, server):
    task_title = "REPRODUCIBLE PREPRINT TRANSFER (Preview --> Preprint)"
    GH_BOT=os.getenv('GH_BOT')
    github_client = Github(GH_BOT)
    task_id = self.request.id
    [owner,repo,provider] = get_owner_repo_provider(repo_url,provider_full_name=True)
    commit_hash = format_commit_hash(repo_url,commit_hash)
    logging.info(f"{owner}{provider}{repo}{commit_hash}")
    remote_path = os.path.join("neurolibre-preview:", "DATA", "book-artifacts", owner, provider, repo, commit_hash + "*")
    try:
        # TODO: improve this, subpar logging.
        f = open("/DATA/synclog.txt", "a")
        f.write(remote_path)
        f.close()
        now = get_time()
        self.update_state(state=states.STARTED, meta={'message': f"Transfer started {now}"})
        gh_template_respond(github_client,"started",task_title,reviewRepository,issue_id,task_id,comment_id, "")
        #logging.info("Calling subprocess")
        process = subprocess.Popen(["/usr/bin/rsync", "-avR", remote_path, "/"], stdout=subprocess.PIPE,stderr=subprocess.STDOUT) 
        output = process.communicate()[0]
        ret = process.wait()
        logging.info(output)
    except subprocess.CalledProcessError as e:
        #logging.info("Subprocess exception")
        gh_template_respond(github_client,"failure",task_title,reviewRepository,issue_id,task_id,comment_id, f"{e.output}")
        self.update_state(state=states.FAILURE, meta={'message': e.output})
    # Check if GET works for the complicated address
    results = book_get_by_params(commit_hash=commit_hash)
    if not results:
        self.update_state(state=states.FAILURE, meta={'message': f"Cannot retreive book at {commit_hash}"})
        gh_template_respond(github_client,"failure",task_title,reviewRepository,issue_id,task_id,comment_id, f"Cannot retreive book at {commit_hash}")
    else:
        # Symlink production book to attain a proper URL
        book_path = os.path.join("/DATA", "book-artifacts", owner, provider, repo, commit_hash + "*")
        iid = "{:05d}".format(issue_id)
        doi_path =  os.path.join("/DATA","10.55458",f"neurolibre.{iid}",".")
        process_mkd = subprocess.Popen(["mkdir", f"/DATA/10.55458/neurolibre.{iid}"], stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
        output_mkd = process_mkd.communicate()[0]
        ret_mkd = process_mkd.wait()
        process_sym = subprocess.Popen(["ln", "-s", book_path, doi_path], stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
        output_sym = process_sym.communicate()[0]
        ret_sym = process_sym.wait()
        logging.info(output_sym)
        book_path_url = os.path.join("book-artifacts", owner, provider, repo, commit_hash,"_build","html")
        # Check if symlink successful
        if os.path.exists(os.path.join("/DATA","10.55458",f"neurolibre.{iid}")):
            message = f"<a href=\"{server}/10.55458/neurolibre.{iid}\">Reproducible Preprint URL (DOI formatted)</a><p><a href=\"{server}/{book_path_url}\">Reproducible Preprint (bare URL)</a></p>"
            gh_template_respond(github_client,"success",task_title,reviewRepository,issue_id,task_id,comment_id, message)
            self.update_state(state=states.SUCCESS, meta={'message': message})
        else:
            self.update_state(state=states.FAILURE, meta={'message': f"Cannot sync book at {commit_hash}"})
            gh_template_respond(github_client,"failure",task_title,reviewRepository,issue_id,task_id,comment_id, output)