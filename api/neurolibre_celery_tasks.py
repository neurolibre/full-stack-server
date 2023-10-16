from celery import Celery
import time
import os 
import json
import subprocess
from celery import states
import pytz
import datetime
from github_client import *
from common import *
from preprint import *
from github import Github, UnknownObjectException
from dotenv import load_dotenv
import logging
import requests
from flask import Response
import shutil
import base64

DOI_PREFIX = "10.55458"
DOI_SUFFIX = "neurolibre"
JOURNAL_NAME = "NeuroLibre"
PAPERS_PATH = "https://neurolibre.org/papers"
PRODUCTION_BINDERHUB = "https://binder-mcgill.conp.cloud"
PREVIEW_SERVER = "https://preview.neurolibre.org"

"""
Configuration START
"""
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')

# IMPORTANT, secrets will not be loaded otherwise.
load_dotenv()

# Setting Redis as both backend and broker
celery_app = Celery('neurolibre_celery_tasks', backend='redis://localhost:6379/1', broker='redis://localhost:6379/0')

celery_app.conf.update(task_track_started=True)

"""
Configuration END
"""

# Set timezone US/Eastern (Montreal)
def get_time():
    """
    To be printed on issue comment updates for 
    background tasks.
    """
    tz = pytz.timezone('US/Eastern')
    now = datetime.datetime.now(tz)
    cur_time = now.strftime('%Y-%m-%d %H:%M:%S %Z')
    return cur_time

@celery_app.task(bind=True)
def sleep_task(self, seconds):
    """
    To test async task functionality
    """
    for i in range(seconds):
        time.sleep(1)
        self.update_state(state='PROGRESS', meta={'remaining': seconds - i - 1})
    return 'done sleeping for {} seconds'.format(seconds)

@celery_app.task(bind=True)
def rsync_data_task(self, comment_id, issue_id, project_name, reviewRepository):
    """
    Uploading data to the production server 
    from the test server.
    """
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
        process = subprocess.Popen(["/usr/bin/rsync", "-avR", remote_path, "/"], stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
        output = process.communicate()[0]
        ret = process.wait()
        #logging.info(output)
    except subprocess.CalledProcessError as e:
        gh_template_respond(github_client,"failure",task_title,reviewRepository,issue_id,task_id,comment_id, f"{e.output}")
        self.update_state(state=states.FAILURE, meta={'message': e.output})
    # Performing a final check
    if os.path.exists(os.path.join("/DATA", project_name)):
        if len(os.listdir(os.path.join("/DATA", project_name))) == 0:
            # Directory exists but empty
            self.update_state(state=states.FAILURE, meta={'message': f"Directory exists but empty {project_name}"})
            gh_template_respond(github_client,"failure",task_title,reviewRepository,issue_id,task_id,comment_id, f"Directory exists but empty: {project_name}")
        else:
            # Directory exists and not empty
            gh_template_respond(github_client,"success",task_title,reviewRepository,issue_id,task_id,comment_id, "Success.")
            self.update_state(state=states.SUCCESS, meta={'message': f"Data sync has been completed for {project_name}"})
    else:
        # Directory does not exist
        self.update_state(state=states.FAILURE, meta={'message': f"Directory does not exist {project_name}"})
        gh_template_respond(github_client,"failure",task_title,reviewRepository,issue_id,task_id,comment_id, f"Directory does not exist: {project_name}")

@celery_app.task(bind=True)
def rsync_book_task(self, repo_url, commit_hash, comment_id, issue_id, reviewRepository, server):
    """
    Moving the book from the test to the production
    server. This book is expected to be built from
    a roboneurolibre repository. 

    Once the book is available on the production server,
    content is symlinked to a DOI formatted directory (Nginx configured) 
    to enable DOI formatted links.
    """
    task_title = "REPRODUCIBLE PREPRINT TRANSFER (Preview --> Preprint)"
    GH_BOT=os.getenv('GH_BOT')
    github_client = Github(GH_BOT)
    task_id = self.request.id
    [owner,repo,provider] = get_owner_repo_provider(repo_url,provider_full_name=True)
    if owner != "roboneurolibre": 
        gh_template_respond(github_client,"failure",task_title,reviewRepository,issue_id,task_id,comment_id, f"Repository is not under roboneurolibre organization!")
        self.update_state(state=states.FAILURE, meta={'message': f"FAILURE: Repository {owner}/{repo} has no roboneurolibre fork."})
        return
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
        self.update_state(state=states.FAILURE, meta={'message': f"Cannot retrieve book at {commit_hash}"})
        gh_template_respond(github_client,"failure",task_title,reviewRepository,issue_id,task_id,comment_id, f"Cannot retrieve book at {commit_hash}")
    else:
        # Symlink production book to attain a proper URL
        book_target_tail = get_book_target_tail(results[0]['book_url'],commit_hash)
        # After the commit hash, the pattern informs whether it is single or multi page.
        # If multi-page _build/html, if single page, should be _build/_page/index/singlehtml
        book_path = os.path.join("/DATA", "book-artifacts", owner, provider, repo, commit_hash , book_target_tail)
        # Here, make sure that all the binderhub links use the lab interface
        enforce_lab_interface(book_path)

        iid = "{:05d}".format(issue_id)
        doi_path =  os.path.join("/DATA","10.55458",f"neurolibre.{iid}")
        process_mkd = subprocess.Popen(["mkdir", doi_path], stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
        output_mkd = process_mkd.communicate()[0]
        ret_mkd = process_mkd.wait()

        for item in os.listdir(book_path):
            source_path = os.path.join(book_path, item)
            target_path = os.path.join(doi_path, item)
            if os.path.isdir(source_path):
                os.symlink(source_path, target_path, target_is_directory=True)
            else:
                os.symlink(source_path, target_path)
        # Check if symlink successful
        if os.path.exists(os.path.join(doi_path)):
            message = f"<a href=\"{server}/10.55458/neurolibre.{iid}\">Reproducible Preprint URL (DOI formatted)</a><p><a href=\"{server}/{book_path}\">Reproducible Preprint (bare URL)</a></p>"
            gh_template_respond(github_client,"success",task_title,reviewRepository,issue_id,task_id,comment_id, message)
            self.update_state(state=states.SUCCESS, meta={'message': message})
        else:
            self.update_state(state=states.FAILURE, meta={'message': f"Cannot sync book at {commit_hash}"})
            gh_template_respond(github_client,"failure",task_title,reviewRepository,issue_id,task_id,comment_id, output)

@celery_app.task(bind=True)
def fork_configure_repository_task(self, payload):
    task_title = "INITIATE PRODUCTION (Fork and Configure)"
    
    GH_BOT=os.getenv('GH_BOT')
    github_client = Github(GH_BOT)
    task_id = self.request.id
    
    now = get_time()
    self.update_state(state=states.STARTED, meta={'message': f"Transfer started {now}"})
    gh_template_respond(github_client,"started",task_title,payload['review_repository'],payload['issue_id'],task_id,payload['comment_id'], "")
    
    book_tested_check = get_test_book_build(PREVIEW_SERVER,True,payload['commit_hash'])
    # Production cannot be started if there's a book at the latest commit hash at which
    # the production is asked for.
    if not book_tested_check['status']:
        msg = f"\n > [!WARNING] \n > A book build could not be found at commit `{payload['commit_hash']}` at {payload['repository_url']}. Production process cannot be started."
        gh_template_respond(github_client,"failure",task_title,payload['review_repository'],payload['issue_id'],task_id,payload['comment_id'], msg, collapsable=False)
        self.update_state(state=states.FAILURE, meta={'message': msg})
        return
    else:
        # Create a record for this.
        fname = f"production_started_record_{payload['issue_id']:05d}.json"
        local_file = os.path.join(get_deposit_dir(payload['issue_id']), fname)
        rec_info = {}
        rec_info['source_repository'] = {}
        rec_info['source_repository']['address'] = payload['repository_url']
        rec_info['source_repository']['commit_hash'] = payload['commit_hash']
        rec_info['source_repository']['book_url'] = book_tested_check['book_url']

    forked_name = gh_forkify_name(payload['repository_url'])
    # First check if a fork already exists.
    fork_exists  = False
    try: 
        github_client.get_repo(forked_name)
        fork_exists = True
    except UnknownObjectException as e:
        gh_template_respond(github_client,"started",task_title,payload['review_repository'],payload['issue_id'],task_id,payload['comment_id'], "Started forking into roboneurolibre.")
        logging.info(e.data['message'] + "--> Forking")

    if not fork_exists:
        try:
            forked_repo = gh_fork_repository(github_client,payload['repository_url'])
        except Exception as e:
            gh_template_respond(github_client,"failure",task_title,payload['review_repository'],payload['issue_id'],task_id,payload['comment_id'], f"Cannot fork the repository into {GH_ORGANIZATION}! \n {str(e)}")
            self.update_state(state=states.FAILURE, meta={'message': f"Cannot fork the repository into {GH_ORGANIZATION}! \n {str(e)}"})
            return

        forked_repo = None
        retry_count = 0
        max_retries = 5

        while retry_count < max_retries and not forked_repo:
            time.sleep(15)
            retry_count += 1
            try:
                forked_repo = github_client.get_repo(forked_name)
            except Exception:
                pass

        if not forked_repo and retry_count == max_retries:
            msg = f"Forked repository is still not available after {max_retries*15} seconds! Please check if the repository is available under roboneurolibre organization, then try again."
            gh_template_respond(github_client,"failure",task_title,payload['review_repository'],payload['issue_id'],task_id,payload['comment_id'], msg)
            self.update_state(state=states.FAILURE, meta={'message': msg})
            return
    else:
        logging.info(f"Fork already exists {payload['repository_url']}, moving on with configurations.")
    
    gh_template_respond(github_client,"started",task_title,payload['review_repository'],payload['issue_id'],task_id,payload['comment_id'], "Forked repo has become available. Proceeding with configuration updates.")

    jb_config = gh_get_jb_config(github_client,forked_name)
    jb_toc = gh_get_jb_toc(github_client,forked_name)

    if not jb_config or not jb_toc:
        msg = f"Could not load _config.yml or _toc.yml under the content directory of {forked_name}"
        gh_template_respond(github_client,"failure",task_title,payload['review_repository'],payload['issue_id'],task_id,payload['comment_id'], msg)
        self.update_state(state=states.FAILURE, meta={'message': msg})
        return

    if 'launch_buttons' not in jb_config:
        jb_config['launch_buttons'] = {}
    # Configure the book to use the production BinderHUB
    jb_config['launch_buttons']['binderhub_url'] = PRODUCTION_BINDERHUB
    # Override this choice.
    jb_config['launch_buttons']['notebook_interface'] = "jupyterlab"

    # Update repository address
    if 'repository' not in jb_config:
        jb_config['repository'] = {}
    # Make sure that there's a link to the forked source.
    jb_config['repository']['url'] = f"https://github.com/{forked_name}"

    # Update configuration file in the forked repo
    response = gh_update_jb_config(github_client,forked_name,jb_config)

    if not response['status']:
        msg = f"Could not update _config.yml for {forked_name}: \n {response['message']}"
        gh_template_respond(github_client,"failure",task_title,payload['review_repository'],payload['issue_id'],task_id,payload['comment_id'], msg)
        self.update_state(state=states.FAILURE, meta={'message': msg})
        return

    jb_toc_new = jb_toc
    if 'parts' in jb_toc:
        jb_toc_new['parts'].append({
            "caption": JOURNAL_NAME,
            "chapters": [{
                "url": f"{PAPERS_PATH}/{DOI_PREFIX}/{DOI_SUFFIX}.{payload['issue_id']:05d}",
                "title": "Citable PDF and archives"
            }]
        })
    
    if 'chapters' in jb_toc:
        jb_toc_new['chapters'].append({
            "url": f"{PAPERS_PATH}/{DOI_PREFIX}/{DOI_SUFFIX}.{payload['issue_id']:05d}",
            "title": "Citable PDF and archives"
        })

    if jb_toc['format'] == 'jb-article' and 'sections' in jb_toc:
        jb_toc_new['sections'].append({
            "url": f"{PAPERS_PATH}/{DOI_PREFIX}/{DOI_SUFFIX}.{payload['issue_id']:05d}",
            "title": "Citable PDF and archives"
        })
    
    # Update TOC file in the forked repo only if the new toc is different
    # otherwise github api will complain.
    if not jb_toc_new != jb_toc:
        response = gh_update_jb_toc(github_client,forked_name,jb_toc)

    if not response['status']:
        msg = f"Could not update toc.yml for {forked_name}: \n {response['message']}"
        gh_template_respond(github_client,"failure",task_title,payload['review_repository'],payload['issue_id'],task_id,payload['comment_id'], msg)
        self.update_state(state=states.FAILURE, meta={'message': f"Could not update _toc.yml for {forked_name}"})
        return

    msg = f"Please confirm that the <a href=\"https://github.com/{forked_name}\">forked repository</a> is available and (<code>_toc.yml</code> and <code>_config.ymlk</code>) properly configured."
    gh_template_respond(github_client,"success",task_title,payload['review_repository'],payload['issue_id'],task_id,payload['comment_id'], msg)
    # Write production record.
    now = get_time()
    rec_info['forked_at'] = now
    rec_info['forked_repository'] = f"https://github.com/{forked_name}"
    with open(local_file, 'w') as outfile:
        json.dump(rec_info, outfile)
    self.update_state(state=states.SUCCESS, meta={'message': msg})


@celery_app.task(bind=True)
def preview_build_book_task(self, payload):

    GH_BOT=os.getenv('GH_BOT')
    github_client = Github(GH_BOT)
    task_id = self.request.id
    binderhub_request = run_binder_build_preflight_checks(payload['repo_url'],
                                                          payload['commit_hash'],
                                                          payload['rate_limit'],
                                                          payload['binder_name'],
                                                          payload['domain_name'])
    lock_filename = get_lock_filename(payload['repo_url'])
    response = requests.get(binderhub_request, stream=True)
    gh_template_respond(github_client,"started",payload['task_title'],payload['review_repository'],payload['issue_id'],task_id,payload['comment_id'], f"Running for: {binderhub_request}")
    if response.ok:
        # Create binder_stream generator object
        def generate():
            #start_time = time.time()
            #messages = []
            #n_updates = 0
            for line in response.iter_lines():
                if line:
                    event_string = line.decode("utf-8")
                    try:
                        event = json.loads(event_string.split(': ', 1)[1])
                        # https://binderhub.readthedocs.io/en/latest/api.html
                        if event.get('phase') == 'failed':
                            message = event.get('message')
                            yield message
                            response.close()
                            #messages.append(message)
                            #gh_template_respond(github_client,"failure","Binder build has failed &#129344;",payload['review_repository'],payload['issue_id'],task_id,payload['comment_id'], messages)
                            # Remove the lock as binder build failed.
                            #app.logger.info(f"[FAILED] BinderHub build {binderhub_request}.")
                            if os.path.exists(lock_filename):
                                os.remove(lock_filename)
                            return
                        message = event.get('message')
                        if message:
                            yield message
                            #messages.append(message)
                            #elapsed_time = time.time() - start_time
                            # Update issue every two minutes
                            #if elapsed_time >= 120:
                            #    n_updates = n_updates + 1
                            #    gh_template_respond(github_client,"started",payload['task_title'] + f" {n_updates*2} minutes passed",payload['review_repository'],payload['issue_id'],task_id,payload['comment_id'], messages)
                            #    start_time = time.time()
                    except GeneratorExit:
                        pass
                    except:
                        pass
        # Use the generator object as the source of flask eventstream response
        binder_response = Response(generate(), mimetype='text/event-stream')
        # Fetch all the yielded messages
    binder_logs = binder_response.get_data(as_text=True)
    binder_logs = "".join(binder_logs)
    # After the upstream closes, check the server if there's 
    # a book built successfully.
    book_status = book_get_by_params(commit_hash=payload['commit_hash'])
    # For now, remove the block either way.
    # The main purpose is to avoid triggering
    # a build for the same request. Later on
    # you may choose to add dead time after a successful build.
    if os.path.exists(lock_filename):
        os.remove(lock_filename)
        # Append book-related response downstream
    if not book_status:
        # These flags will determine how the response will be 
        # interpreted and returned outside the generator
        gh_template_respond(github_client,"failure","Binder build has failed &#129344;",payload['review_repository'],payload['issue_id'],task_id,payload['comment_id'], "The next comment will forward the logs")
        issue_comment = []
        msg = f"<p>&#129344; We ran into a problem building your book. Please see the log files below.</p><details><summary> <b>BinderHub build log</b> </summary><pre><code>{binder_logs}</code></pre></details><p>If the BinderHub build looks OK, please see the Jupyter Book build log(s) below.</p>"
        issue_comment.append(msg)
        owner,repo,provider = get_owner_repo_provider(payload['repo_url'],provider_full_name=True)
        # Retrieve book build and execution report logs.
        book_logs = book_log_collector(owner,repo,provider,payload['commit_hash'])
        issue_comment.append(book_logs)
        msg = "<p>&#128030; After inspecting the logs above, you can interactively debug your notebooks on our <a href=\"https://binder.conp.cloud\">BinderHub server</a>.</p> <p>For guidelines, please see <a href=\"https://docs.neurolibre.org/en/latest/TEST_SUBMISSION.html#debugging-for-long-neurolibre-submission\">the relevant documentation.</a></p>"
        issue_comment.append(msg)
        issue_comment = "\n".join(issue_comment)
        # Send a new comment
        gh_create_comment(github_client, payload['review_repository'],payload['issue_id'],issue_comment)
    else:
        gh_template_respond(github_client,"success","Successfully built", payload['review_repository'],payload['issue_id'],task_id,payload['comment_id'], f":confetti_ball: Roboneuro will send you the book URL.")
        issue_comment = []
        try:
            paper_string = gh_get_paper_markdown(github_client,payload['repo_url'])
            fm = parse_front_matter(paper_string)
            prompt = f"Based on the title {fm['title']} and keywords of {fm['tags']}, congratulate the authors by saying a few nice things about the neurolibre reproducible preprint (NRP) the authors just successfully built! Keep it short (2 sentences) and witty."
            gpt_response = get_gpt_response(prompt)
            issue_comment = f":robot::speech_balloon::confetti_ball::rocket: \n {gpt_response} \n\n :hibiscus: Take a loot at the [latest version of your NRP]({book_status[0]['book_url']})! :hibiscus: \n --- \n > [!IMPORTANT] \n > Please make sure the figures are displayed correctly, code cells are collapsible, and that BinderHub execution is successful."
        except Exception as e:
            logging.info(f"{str(e)}")
            issue_comment = f":confetti_ball::confetti_ball::confetti_ball: Good news! \n\n :hibiscus: Take a loot at the [latest version of your NRP]({book_status[0]['book_url']})"
        gh_create_comment(github_client, payload['review_repository'],payload['issue_id'],issue_comment)

@celery_app.task(bind=True)
def zenodo_create_buckets_task(self, payload):
    
    GH_BOT=os.getenv('GH_BOT')
    github_client = Github(GH_BOT)
    task_id = self.request.id

    gh_template_respond(github_client,"started",payload['task_title'], payload['review_repository'],payload['issue_id'],task_id,payload['comment_id'])

    fname = f"zenodo_deposit_NeuroLibre_{payload['issue_id']:05d}.json"
    local_file = os.path.join(get_deposit_dir(payload['issue_id']), fname)

    if os.path.exists(local_file):
        msg = f"Zenodo records already exist for this submission on NeuroLibre servers: {fname}. Please proceed with data uploads if the records are valid. Flush the existing records otherwise."
        gh_template_respond(github_client,"exists",payload['task_title'], payload['review_repository'],payload['issue_id'],task_id,payload['comment_id'],msg)
        self.update_state(state=states.FAILURE, meta={'message': msg})
        return

    data = payload['paper_data']

    # We need to go through some affiliation mapping here.
    affiliation_mapping = {str(affiliation['index']): affiliation['name'] for affiliation in data['affiliations']}
    first_affiliations = []
    for author in data['authors']:
        if isinstance(author['affiliation'],int):
            affiliation_index = author['affiliation']
        else:
            affiliation_indices = [affiliation_index for affiliation_index in author['affiliation'].split(',')]
            affiliation_index = affiliation_indices[0]
        first_affiliation = affiliation_mapping[str(affiliation_index)]
        first_affiliations.append(first_affiliation)

    for ii in range(len(data['authors'])):
        data['authors'][ii]['affiliation'] = first_affiliations[ii]
    
    # To deal with some typos, also with orchid :) 
    valid_field_names = {'name', 'orcid', 'affiliation'}
    for author in data['authors']:
        invalid_fields = []
        for field in author:
            if field not in valid_field_names:
                invalid_fields.append(field)
        
        for invalid_field in invalid_fields:
            valid_field = None
            for valid_name in valid_field_names:
                if valid_name.lower() in invalid_field.lower() or (valid_name == 'orcid' and invalid_field.lower() == 'orchid'):
                    valid_field = valid_name
                    break
            
            if valid_field:
                author[valid_field] = author.pop(invalid_field)
        
        if 'equal-contrib' in author:
            author.pop('equal-contrib')

        if 'corresponding' in author:
            author.pop('corresponding')

        # if author.get('orcid') is None:
        #     author.pop('orcid')

    collect = {}
    for archive_type in payload['archive_assets']:
                gh_template_respond(github_client,"started",payload['task_title'], payload['review_repository'],payload['issue_id'],task_id,payload['comment_id'], f"Creating Zenodo buckets for {archive_type}")
                r = zenodo_create_bucket(data['title'],
                                         archive_type,
                                         data['authors'],
                                         payload['repository_url'],
                                         payload['issue_id'])
                collect[archive_type] = r
                # Rate limit
                time.sleep(2)
    
    if {k: v for k, v in collect.items() if 'reason' in v}:
        # This means at least one of the deposits has failed.
        logging.info(f"Caught an issue with the deposit. A record (JSON) will not be created.")

        # Delete deposition if succeeded for a certain resource
        remove_dict = {k: v for k, v in collect.items() if not 'reason' in v }
        for key in remove_dict:
            logging.info("Deleting " + remove_dict[key]["links"]["self"])
            tmp = zenodo_delete_bucket(remove_dict[key]["links"]["self"])
            time.sleep(1)
            # Returns 204 if successful, cast str to display
            collect[key + "_deleted"] = str(tmp)
        gh_template_respond(github_client,"failure",payload['task_title'], payload['review_repository'],payload['issue_id'],task_id,payload['comment_id'], f"{collect}")
    else:
        # This means that all requested deposits are successful
        print(f'Writing {local_file}...')
        with open(local_file, 'w') as outfile:
            json.dump(collect, outfile)
        gh_template_respond(github_client,"success",payload['task_title'], payload['review_repository'],payload['issue_id'],task_id,payload['comment_id'], f"Zenodo records have been created successfully: \n {collect}")

@celery_app.task(bind=True)
def zenodo_flush_task(self,payload):
    
    GH_BOT=os.getenv('GH_BOT')
    github_client = Github(GH_BOT)
    task_id = self.request.id

    gh_template_respond(github_client,"started",payload['task_title'], payload['review_repository'],payload['issue_id'],task_id,payload['comment_id'])

    zenodo_record = get_zenodo_deposit(payload['issue_id'])
    msg = []
    prog = {}
    items = zenodo_record.keys()
    for item in items:
        # Delete the bucket first.
        record_name = item_to_record_name(item)
        delete_response = zenodo_delete_bucket(zenodo_record[item]['links']['self'])
        if delete_response.status_code == 204:
            msg.append(f"\n Deleted {item} deposit successfully.")
            prog[item] = True
            # Flush ALL the upload records (json) associated with the item
            tmp_record = glob.glob(os.path.join(get_deposit_dir(payload['issue_id']),f"zenodo_uploaded_{item}_NeuroLibre_{payload['issue_id']:05d}_*.json"))
            if tmp_record:
                os.remove(tmp_record)
                msg.append(f"\n Deleted {tmp_record} record from the server.")
            else:
                msg.append(f"\n {tmp_record} did not exist.")
            # Flush ALL the uploaded files associated with the item
            tmp_file = glob.glob(os.path.join(get_archive_dir(payload['issue_id']),f"{record_name}_10.55458_NeuroLibre_{payload['issue_id']:05d}_*.zip"))
            if tmp_file:
                os.remove(tmp_file)
                msg.append(f"\n Deleted {tmp_file} record from the server.")
            else:
                msg.append(f"{tmp_file} did not exist.")
        elif delete_response.status_code == 403:
            prog[item] = False
            msg.append(f"\n The {item} archive has already been published, cannot be deleted.")
            gh_template_respond(github_client,"failure",payload['task_title'], payload['review_repository'],payload['issue_id'],task_id,payload['comment_id'],"".join(msg))
        elif delete_response.status_code == 410:
            prog[item] = False
            msg.append(f"\n The {item} deposit does not exist.")
            gh_template_respond(github_client,"failure",payload['task_title'], payload['review_repository'],payload['issue_id'],task_id,payload['comment_id'],"".join(msg))
    
    # Update the issue comment
    gh_template_respond(github_client,"started",payload['task_title'], payload['review_repository'],payload['issue_id'],task_id,payload['comment_id'],"".join(msg))

    check_deposits = prog.values()
    if all(check_deposits):
        fname = f"zenodo_deposit_NeuroLibre_{payload['issue_id']:05d}.json"
        local_file = os.path.join(get_deposit_dir(payload['issue_id']), fname)
        os.remove(local_file)
        msg.append(f"\n Deleted old deposit records from the server: {local_file}")
        gh_template_respond(github_client,"success",payload['task_title'], payload['review_repository'],payload['issue_id'],task_id,payload['comment_id'],"".join(msg))
    else:
        msg.append(f"\n ERROR: At least one of the records could NOT have been deleted from Zenodo. Existing deposit file will NOT be deleted.")
        gh_template_respond(github_client,"failure",payload['task_title'], payload['review_repository'],payload['issue_id'],task_id,payload['comment_id'],"".join(msg))

@celery_app.task(bind=True)
def zenodo_upload_book_task(self, payload):

    GH_BOT=os.getenv('GH_BOT')
    github_client = Github(GH_BOT)
    task_id = self.request.id
    
    gh_template_respond(github_client,"started",payload['task_title'], payload['review_repository'],payload['issue_id'],task_id,payload['comment_id'])

    owner,repo,provider = get_owner_repo_provider(payload['repository_url'],provider_full_name=True)
    
    fork_url = f"https://{provider}/roboneurolibre/{repo}"
    commit_fork = format_commit_hash(fork_url,"HEAD")
    
    results = book_get_by_params(commit_hash=commit_fork)
    # Need to manage for single or multipage location.
    book_target_tail = get_book_target_tail(results[0]['book_url'],commit_fork)
    local_path = os.path.join("/DATA", "book-artifacts", "roboneurolibre", provider, repo, commit_fork, book_target_tail)
    # Descriptive file name
    zenodo_file = os.path.join(get_archive_dir(payload['issue_id']),f"JupyterBook_10.55458_NeuroLibre_{payload['issue_id']:05d}_{commit_fork[0:6]}")
    # Zip it!
    shutil.make_archive(zenodo_file, 'zip', local_path)
    zpath = zenodo_file + ".zip"

    response = zenodo_upload_book(zpath,payload['bucket_url'],payload['issue_id'],commit_fork)

    if not response:
        gh_template_respond(github_client,"failure",payload['task_title'], payload['review_repository'],payload['issue_id'],task_id,payload['comment_id'], f"Cannot upload {zpath} to {payload['bucket_url']}")
    else:
        tmp = f"zenodo_uploaded_book_NeuroLibre_{payload['issue_id']:05d}_{commit_fork[0:6]}.json"
        log_file = os.path.join(get_deposit_dir(payload['issue_id']), tmp)
        with open(log_file, 'w') as outfile:
                json.dump(response.json(), outfile)
        gh_template_respond(github_client,"success",payload['task_title'], payload['review_repository'],payload['issue_id'],task_id,payload['comment_id'], f"Successful {zpath} to {payload['bucket_url']}")

@celery_app.task(bind=True)
def zenodo_upload_data_task(self,payload):
        
        GH_BOT=os.getenv('GH_BOT')
        github_client = Github(GH_BOT)
        task_id = self.request.id

        gh_template_respond(github_client,"started",payload['task_title'], payload['review_repository'],payload['issue_id'],task_id,payload['comment_id'])

        owner,repo,provider = get_owner_repo_provider(payload['repository_url'],provider_full_name=True)
        fork_url = f"https://{provider}/roboneurolibre/{repo}"
        commit_fork = format_commit_hash(fork_url,"HEAD")
        record_name = item_to_record_name("data")

        expect = os.path.join(get_archive_dir(payload['issue_id']),f"{record_name}_10.55458_NeuroLibre_{payload['issue_id']:05d}_{commit_fork[0:6]}.zip")
        check_data = os.path.exists(expect)

        # Get repo2data project name...
        project_name = gh_get_project_name(github_client,payload['repository_url'])

        if check_data:
            logging.info(f"Compressed data already exists {record_name}_10.55458_NeuroLibre_{payload['issue_id']:05d}_{commit_fork[0:6]}.zip")
            tar_file = expect
        else:
            # We will archive the data synced from the test server. (item_arg is the project_name, indicating that the 
            # data is stored at the /DATA/project_name folder)
            local_path = os.path.join("/DATA", project_name)
            # Descriptive file name
            zenodo_file = os.path.join(get_archive_dir(payload['issue_id']),f"{record_name}_10.55458_NeuroLibre_{payload['issue_id']:05d}_{commit_fork[0:6]}")
            # Zip it!
            shutil.make_archive(zenodo_file, 'zip', local_path)
            tar_file = zenodo_file + ".zip"

        response = zenodo_upload_item(tar_file,payload['bucket_url'],payload['issue_id'],commit_fork,"data")
        if not response:
            msg = f"Cannot upload {tar_file} to {payload['bucket_url']}"
            gh_template_respond(github_client,"failure",payload['task_title'], payload['review_repository'],payload['issue_id'],task_id,payload['comment_id'], msg)
            self.update_state(state=states.FAILURE, meta={'message': msg})
        else:
            tmp = f"zenodo_uploaded_data_NeuroLibre_{payload['issue_id']:05d}_{commit_fork[0:6]}.json"
            log_file = os.path.join(get_deposit_dir(payload['issue_id']), tmp)
            with open(log_file, 'w') as outfile:
                    json.dump(response.json(), outfile)
            msg = f"Successful {tar_file} to {payload['bucket_url']}"
            gh_template_respond(github_client,"success",payload['task_title'], payload['review_repository'],payload['issue_id'],task_id,payload['comment_id'], msg)
            self.update_state(state=states.SUCCESS, meta={'message': msg})

@celery_app.task(bind=True)
def zenodo_upload_repository_task(self, payload):

    GH_BOT=os.getenv('GH_BOT')
    github_client = Github(GH_BOT)
    task_id = self.request.id
    
    gh_template_respond(github_client,"started",payload['task_title'], payload['review_repository'],payload['issue_id'],task_id,payload['comment_id'])

    owner,repo,provider = get_owner_repo_provider(payload['repository_url'],provider_full_name=True)
    
    fork_url = f"https://{provider}/roboneurolibre/{repo}"
    commit_fork = format_commit_hash(fork_url,"HEAD")
    
    default_branch = get_default_branch(github_client,fork_url)

    download_url = f"{fork_url}/archive/refs/heads/{default_branch}.zip"

    zenodo_file = os.path.join(get_archive_dir(payload['issue_id']),f"GitHubRepo_10.55458_NeuroLibre_{payload['issue_id']:05d}_{commit_fork[0:6]}.zip")

    # REFACTOR HERE AND MANAGE CONDITIONS CLEANER.
    # Try main first
    resp = os.system(f"wget -O {zenodo_file} {download_url}")
    if resp != 0:
        gh_template_respond(github_client,"failure",payload['task_title'], payload['review_repository'],payload['issue_id'],task_id,payload['comment_id'], f"Cannot download: {download_url}")
        self.update_state(state=states.FAILURE, meta={'message': f"Cannot download {download_url}"})
        return
    else:
        response = zenodo_upload_repository(zenodo_file,payload['bucket_url'],payload['issue_id'],commit_fork) 
        if not response:
            gh_template_respond(github_client,"failure",payload['task_title'], payload['review_repository'],payload['issue_id'],task_id,payload['comment_id'], f"Cannot upload {zenodo_file} to {payload['bucket_url']}")
        else:
            tmp = f"zenodo_uploaded_repository_NeuroLibre_{payload['issue_id']:05d}_{commit_fork[0:6]}.json"
            log_file = os.path.join(get_deposit_dir(payload['issue_id']), tmp)
            with open(log_file, 'w') as outfile:
                json.dump(response.json(), outfile)
            msg = f"Successful {zenodo_file} to {payload['bucket_url']}"        
            gh_template_respond(github_client,"success",payload['task_title'], payload['review_repository'],payload['issue_id'],task_id,payload['comment_id'], msg)
            self.update_state(state=states.SUCCESS, meta={'message': msg})
            
@celery_app.task(bind=True)
def zenodo_upload_docker_task(self, payload):

    GH_BOT=os.getenv('GH_BOT')
    github_client = Github(GH_BOT)
    task_id = self.request.id
    
    gh_template_respond(github_client,"started",payload['task_title'], payload['review_repository'],payload['issue_id'],task_id,payload['comment_id'])

    owner,repo,provider = get_owner_repo_provider(payload['repository_url'],provider_full_name=True)
    
    fork_url = f"https://{provider}/roboneurolibre/{repo}"
    commit_fork = format_commit_hash(fork_url,"HEAD")

    record_name = item_to_record_name("docker")

    tar_file = os.path.join(get_archive_dir(payload['issue_id']),f"{record_name}_10.55458_NeuroLibre_{payload['issue_id']:05d}_{commit_fork[0:6]}.tar.gz")
    check_docker = os.path.exists(tar_file)

    if check_docker:
        msg = "Docker image already exists, uploading to zenodo."
        gh_template_respond(github_client,"started",payload['task_title'] + " `uploading (3/3)`", payload['review_repository'],payload['issue_id'],task_id,payload['comment_id'],msg)
        # If image exists but could not upload due to a previous issue.
        response = zenodo_upload_item(tar_file,payload['bucket_url'],payload['issue_id'],commit_fork,"docker")
        if (not response) or (isinstance(response, requests.exceptions.RequestException)) :
            gh_template_respond(github_client,"failure",payload['task_title'], payload['review_repository'],payload['issue_id'],task_id,payload['comment_id'], f"{str(response)}")
            self.update_state(state=states.FAILURE, meta={'message': f"ERROR {fork_url}: {str(response)}"})
        elif (isinstance(response, requests.Response)) and (response.status_code > 300):
            gh_template_respond(github_client,"failure",payload['task_title'], payload['review_repository'],payload['issue_id'],task_id,payload['comment_id'], f"{response.text}")
            self.update_state(state=states.FAILURE, meta={'message': f"ERROR {fork_url}: {response.text}"})
        else:
            tmp = f"zenodo_uploaded_docker_NeuroLibre_{payload['issue_id']:05d}_{commit_fork[0:6]}.json"
            log_file = os.path.join(get_deposit_dir(payload['issue_id']), tmp)
            with open(log_file, 'w') as outfile:
                    json.dump(response.json(), outfile)
            gh_template_respond(github_client,"success",payload['task_title'], payload['review_repository'],payload['issue_id'],task_id,payload['comment_id'], f"Successful {tar_file} to {payload['bucket_url']}")
            self.update_state(state=states.SUCCESS, meta={'message': f"SUCCESS: Docker image upload for {owner}/{repo} at {payload['commit_hash']} has succeeded."})
    else:
        # Get the lookup_table.tsv entry (from the preview server) for the fork_url
        lut = get_resource_lookup(PREVIEW_SERVER,True,fork_url)

        if not lut:
            # Terminate ERROR
            msg = f"Looks like there's not a successful book build record for {fork_url}"
            gh_template_respond(github_client,"failure",payload['task_title'], payload['review_repository'],payload['issue_id'],task_id,payload['comment_id'], msg)
            self.update_state(state=states.FAILURE, meta={'message': msg})
            return

        msg = f"Found docker image: \n {lut}"
        gh_template_respond(github_client,"started",payload['task_title'], payload['review_repository'],payload['issue_id'],task_id,payload['comment_id'],msg)

        # Login to the private registry to pull images
        r = docker_login()
        
        if not r['status']:
            msg = f"Cannot login to NeuroLibre private docker registry. \n {r['message']}"
            gh_template_respond(github_client,"failure",payload['task_title'], payload['review_repository'],payload['issue_id'],task_id,payload['comment_id'], msg)
            self.update_state(state=states.FAILURE, meta={'message': msg})
            return

        msg = f"Pulling docker image: \n {lut['docker_image']}"
        gh_template_respond(github_client,"started",payload['task_title'] + " `pulling (1/3)`", payload['review_repository'],payload['issue_id'],task_id,payload['comment_id'],msg)

        # The lookup table (lut) should contain a docker image (see get_resource_lookup)
        r = docker_pull(lut['docker_image'])
        if not r['status']:
            msg = f"Cannot pull the docker image \n {r['message']}"
            gh_template_respond(github_client,"failure",payload['task_title'], payload['review_repository'],payload['issue_id'],task_id,payload['comment_id'], msg)
            self.update_state(state=states.FAILURE, meta={'message': msg})
            return
        
        msg = f"Exporting docker image: \n {lut['docker_image']}"
        gh_template_respond(github_client,"started",payload['task_title'] + " `exporting (2/3)`", payload['review_repository'],payload['issue_id'],task_id,payload['comment_id'],msg)

        r = docker_save(lut['docker_image'],payload['issue_id'],commit_fork)
        if not r[0]['status']:
            msg = f"Cannot save the docker image \n {r[0]['message']}"
            gh_template_respond(github_client,"failure",payload['task_title'], payload['review_repository'],payload['issue_id'],task_id,payload['comment_id'], msg)
            self.update_state(state=states.FAILURE, meta={'message': msg})
            return

        tar_file = r[1]

        msg = f"Uploading docker image: \n {tar_file}"
        gh_template_respond(github_client,"started",payload['task_title'] + " `uploading (3/3)`", payload['review_repository'],payload['issue_id'],task_id,payload['comment_id'],msg)

        response = zenodo_upload_item(tar_file,payload['bucket_url'],payload['issue_id'],commit_fork,"docker")
        logging.info(f"{str(response)}")
        if not response:
            msg = f"Cannot upload {tar_file} to {payload['bucket_url']}"
            gh_template_respond(github_client,"failure",payload['task_title'], payload['review_repository'],payload['issue_id'],task_id,payload['comment_id'], msg)
            self.update_state(state=states.FAILURE, meta={'message': msg})
        else:
            tmp = f"zenodo_uploaded_docker_NeuroLibre_{payload['issue_id']:05d}_{commit_fork[0:6]}.json"
            log_file = os.path.join(get_deposit_dir(payload['issue_id']), tmp)
            with open(log_file, 'w') as outfile:
                    json.dump(response.json(), outfile)
            msg = f"Successful {tar_file} to {payload['bucket_url']}"
            gh_template_respond(github_client,"success",payload['task_title'], payload['review_repository'],payload['issue_id'],task_id,payload['comment_id'], msg)
            self.update_state(state=states.SUCCESS, meta={'message': msg})
        r = docker_logout()
        # No need to break the operation this fails, just log.
        if not r['status']:
            logging.info("Problem with docker logout.")

@celery_app.task(bind=True)
def zenodo_publish_task(self, payload):
    
    GH_BOT=os.getenv('GH_BOT')
    github_client = Github(GH_BOT)
    task_id = self.request.id
    
    gh_template_respond(github_client,"started",payload['task_title'], payload['review_repository'],payload['issue_id'],task_id,payload['comment_id'])

    response = zenodo_publish(payload['issue_id'])
    
    if response == "no-record-found":
        msg = "<br> :neutral_face: I could not find any Zenodo-related records on NeuroLibre servers. Maybe start with <code>roboneuro zenodo create buckets</code>?"
        gh_template_respond(github_client,"failure",payload['task_title'], payload['review_repository'],payload['issue_id'],task_id,payload['comment_id'], msg)
        self.update_state(state=states.FAILURE, meta={'message': msg})
        return
    else:
        # Confirm that all items are published.
        # TODO: Check this 
        publish_status = zenodo_confirm_status(payload['issue_id'],"published")
        # If all items are published, success. Add DOIs.
        if publish_status[0]:
            prompt = "First state that you will issue commands to set DOIs for the reproducibility assets, then you'll talk to yourself a bit. But reassure in a funny way that there's nothing to worry about because you are not an artificial general intelligence (yet). Keep it to a few sentences."
            gpt_response = get_gpt_response(prompt)
            gh_template_respond(github_client,"success",payload['task_title'], payload['review_repository'],payload['issue_id'],task_id,payload['comment_id'], f"Congrats! Reproducibility assets have been successfully archived and published :rocket: \n\n {gpt_response}", False)
            # Set DOIs. This part is a little crazy, because roboneuro will be 
            # telling itself what to do. Like father, like son.
            dois = zenodo_collect_dois(payload['issue_id'])
            for key in dois.keys():
                command = f"@roboneuro set {dois[key]} as {key} archive"
                gh_create_comment(github_client,payload['review_repository'],payload['issue_id'],command)
                time.sleep(1)
        else:
            # Some one None 
            response.append(f"\n Looks like there's a problem. {publish_status[1]} reproducibility assets are archived.")
            msg = "\n".join(response)
            gh_template_respond(github_client,"failure",payload['task_title'], payload['review_repository'],payload['issue_id'],task_id,payload['comment_id'], msg, False)
            self.update_state(state=states.FAILURE, meta={'message': msg})

### DUPLICATION FOR NOW, SAVING THE DAY.

@celery_app.task(bind=True)
def preview_build_book_test_task(self, payload):

    task_id = self.request.id
    owner,repo,provider = get_owner_repo_provider(payload['repo_url'],provider_full_name=True)
    binderhub_request = run_binder_build_preflight_checks(payload['repo_url'],
                                                          payload['commit_hash'],
                                                          payload['rate_limit'],
                                                          payload['binder_name'],
                                                          payload['domain_name'])
    lock_filename = get_lock_filename(payload['repo_url'])
    response = requests.get(binderhub_request, stream=True)
    mail_body = f"Runtime environment build has been started <code>{task_id}</code> If successful, it will be followed by the Jupyter Book build."
    send_email_celery(payload['email'],payload['mail_subject'],mail_body)
    now = get_time()
    self.update_state(state=states.STARTED, meta={'message': f"IN PROGRESS: Build for {owner}/{repo} at {payload['commit_hash']} has been running since {now}"})
    #gh_template_respond(github_client,"started",payload['task_title'],payload['review_repository'],payload['issue_id'],task_id,payload['comment_id'], f"Running for: {binderhub_request}")
    if response.ok:
        # Create binder_stream generator object
        def generate():
            #start_time = time.time()
            #messages = []
            #n_updates = 0
            for line in response.iter_lines():
                if line:
                    event_string = line.decode("utf-8")
                    try:
                        event = json.loads(event_string.split(': ', 1)[1])
                        # https://binderhub.readthedocs.io/en/latest/api.html
                        if event.get('phase') == 'failed':
                            message = event.get('message')
                            yield message
                            response.close()
                            #messages.append(message)
                            #gh_template_respond(github_client,"failure","Binder build has failed &#129344;",payload['review_repository'],payload['issue_id'],task_id,payload['comment_id'], messages)
                            # Remove the lock as binder build failed.
                            #app.logger.info(f"[FAILED] BinderHub build {binderhub_request}.")
                            if os.path.exists(lock_filename):
                                os.remove(lock_filename)
                            return
                        message = event.get('message')
                        if message:
                            yield message
                            #messages.append(message)
                            #elapsed_time = time.time() - start_time
                            # Update issue every two minutes
                            #if elapsed_time >= 120:
                            #    n_updates = n_updates + 1
                            #    gh_template_respond(github_client,"started",payload['task_title'] + f" {n_updates*2} minutes passed",payload['review_repository'],payload['issue_id'],task_id,payload['comment_id'], messages)
                            #    start_time = time.time()
                    except GeneratorExit:
                        pass
                    except:
                        pass
        # Use the generator object as the source of flask eventstream response
        binder_response = Response(generate(), mimetype='text/event-stream')
        # Fetch all the yielded messages
    binder_logs = binder_response.get_data(as_text=True)
    binder_logs = "".join(binder_logs)
    # After the upstream closes, check the server if there's 
    # a book built successfully.
    book_status = book_get_by_params(commit_hash=payload['commit_hash'])
    exec_error = book_execution_errored(owner,repo,provider,payload['commit_hash'])
    # For now, remove the block either way.
    # The main purpose is to avoid triggering
    # a build for the same request. Later on
    # you may choose to add dead time after a successful build.
    if os.path.exists(lock_filename):
        os.remove(lock_filename)
        # Append book-related response downstream
    if not book_status or exec_error:
        # These flags will determine how the response will be 
        # interpreted and returned outside the generator
        #gh_template_respond(github_client,"failure","Binder build has failed &#129344;",payload['review_repository'],payload['issue_id'],task_id,payload['comment_id'], "The next comment will forward the logs")
        issue_comment = []
        msg = f"<p>&#129344; We ran into a problem building your book. Please see the log files below.</p><details><summary> <b>BinderHub build log</b> </summary><pre><code>{binder_logs}</code></pre></details><p>If the BinderHub build looks OK, please see the Jupyter Book build log(s) below.</p>"
        issue_comment.append(msg)
        owner,repo,provider = get_owner_repo_provider(payload['repo_url'],provider_full_name=True)
        # Retrieve book build and execution report logs.
        book_logs = book_log_collector(owner,repo,provider,payload['commit_hash'])
        issue_comment.append(book_logs)
        msg = "<p>&#128030; After inspecting the logs above, you can interactively debug your notebooks on our <a href=\"https://test.conp.cloud\">BinderHub server</a>.</p> <p>For guidelines, please see <a href=\"https://docs.neurolibre.org/en/latest/TEST_SUBMISSION.html#debugging-for-long-neurolibre-submission\">the relevant documentation.</a></p>"
        issue_comment.append(msg)
        issue_comment = "\n".join(issue_comment)
        tmp_log = write_html_to_temp_directory(payload['commit_hash'], issue_comment)
        body = "<p>&#129344; We ran into a problem building your book. Please download the log file attached and open in your web browser.</p>"
        send_email_with_html_attachment_celery(payload['email'], payload['mail_subject'], body, tmp_log)
        self.update_state(state=states.FAILURE, meta={'message': f"FAILURE: Build for {owner}/{repo} at {payload['commit_hash']} has failed"})
    else:
        #gh_template_respond(github_client,"success","Successfully built", payload['review_repository'],payload['issue_id'],task_id,payload['comment_id'], f"The next comment will forward the logs")
        #issue_comment = []
        mail_body = f"Book build successful: {book_status[0]['book_url']}"
        send_email_celery(payload['email'],payload['mail_subject'],mail_body)
        self.update_state(state=states.SUCCESS, meta={'message': f"SUCCESS: Build for {owner}/{repo} at {payload['commit_hash']} has succeeded."})

def send_email_celery(to_email, subject, body):
    sg_api_key = os.getenv('SENDGRID_API_KEY')
    sender_email = "no-reply@neurolibre.org"

    message = Mail(
        from_email=sender_email,
        to_emails=to_email,
        subject=subject,
        html_content=body
    )

    try:
        sg = SendGridAPIClient(sg_api_key)
        response = sg.send(message)
        print("Email sent successfully!")
        print(response.status_code)
        print(response.body)
        print(response.headers)
    except Exception as e:
        print("Error sending email:", str(e))



def send_email_with_html_attachment_celery(to_email, subject, body, attachment_path):
    sg_api_key = os.getenv('SENDGRID_API_KEY')
    sender_email = "no-reply@neurolibre.org"

    message = Mail(
        from_email=sender_email,
        to_emails=to_email,
        subject=subject,
        html_content=body
    )

    with open(attachment_path, "rb") as file:
        data = file.read()

    encoded_data = base64.b64encode(data).decode()

    # Add the attachment to the email with MIME type "text/html"
    attachment = Attachment(
        FileContent(encoded_data),
        FileName(os.path.basename(attachment_path)),
        FileType("text/html"),
        Disposition("attachment")
    )
    message.attachment = attachment

    try:
        sg = SendGridAPIClient(sg_api_key)
        response = sg.send(message)
        print("Email sent successfully!")
        print(response.status_code)
        print(response.body)
        print(response.headers)
    except Exception as e:
        print("Error sending email:", str(e))

def write_html_to_temp_directory(commit_sha, logs):
    with tempfile.TemporaryDirectory() as temp_dir:
        file_path = os.path.join("/DATA","api_build_logs", f"logs_{commit_sha[:7]}.html")

        with open(file_path, "w+") as f:
            f.write("<!DOCTYPE html>\n")
            f.write("<html lang=\"en\" class=\"no-js\">\n")
            f.write("<style>body { background-color: #fbdeda; color: black; font-family: monospace; } pre { background-color: #222222; border: none; color: white; padding: 10px; margin: 10px; overflow: auto; } code { font-family: monospace; font-size: 12px; background-color: #222222; color: white; border-radius: 5px; padding: 2px; } </style>\n")
            f.write("<body>\n")
            f.write(f"{logs}")
            f.write("</body></html>\n")
    
    return file_path

@celery_app.task(bind=True)
def preprint_build_pdf_draft(self, payload):

    GH_BOT=os.getenv('GH_BOT')
    github_client = Github(GH_BOT)
    task_id = self.request.id
    gh_template_respond(github_client,"started",payload['task_title'], payload['review_repository'],payload['issue_id'],task_id,payload['comment_id'])
    target_path = os.path.join('/DATA/10.55458/draft',f"{payload['issue_id']:05d}")
    # Remove the directory if it already exists.
    if os.path.exists(target_path):
        shutil.rmtree(target_path)
    try:
        gh_clone_repository(payload['repository_url'], target_path, depth=1)
    except Exception as e: 
        gh_template_respond(github_client,"failure",payload['task_title'], payload['review_repository'],payload['issue_id'],task_id,payload['comment_id'], str(e))
        self.update_state(state=states.FAILURE, meta={'message': str(e)})
        return
    # Crawl notebooks for the citation text and append it to paper.md, update bib.
    res = create_extended_pdf_sources(target_path, payload['issue_id'],payload['repository_url'])
    if res['status']:
        try:
            process = subprocess.Popen(["docker", "run","--rm", "-v", f"{target_path}:/data", "-u", "ubuntu:www-data", "neurolibre/inara:latest","-o", "neurolibre", "./paper.md"], stdout=subprocess.PIPE,stderr=subprocess.STDOUT) 
            output = process.communicate()[0]
            ret = process.wait()
            logging.info(output)
            # If it hits here, paper.pdf should have been created.
            gh_template_respond(github_client,"success","Successfully built", payload['review_repository'],payload['issue_id'],task_id,payload['comment_id'], f"Roboneuro will post a new comment to share the results and provide an explanation of the next steps.")
            comment = f"&#128209; Extended PDF has been compiled! \n \
                    \n ### For the submitting author \n \
                    \n 1. 👀  Please review the [extended PDF](https://preprint.neurolibre.org/10.55458/draft/{payload['issue_id']:05d}/paper.pdf) and verify that all references are accurately included. If everything is correct, please proceed to the next steps. **If not, please make the necessary adjustments in the source documents.** \
                    \n 2. ⬇️ [Download the updated `paper.md`](https://preprint.neurolibre.org/10.55458/draft/{payload['issue_id']:05d}/paper.md). \n \
                    \n 3. ⬇️ [Download the updated `paper.bib`](https://preprint.neurolibre.org/10.55458/draft/{payload['issue_id']:05d}/paper.bib). \n \
                    \n 4. ℹ️ Please read and confirm the following: \n \
                    \n > [!IMPORTANT] \
                    \n > We have added a note in the extended PDF to inform the readers that the narrative content from your notebook content has been automatically added to credit the referenced sources. This note includes citations to the articles \
                    explaining the [NeuroLibre workflow](https://doi.org/10.31219/osf.io/h89js), [integrated research objects](https://doi.org/10.1371/journal.pcbi.1009651), and the Canadian Open Neuroscience Platform ([CONP](https://conp.ca)). _If you prefer not to include them, please remove the respective citation directives \
                    in the updated `paper.md` before pushing the file to your repository._ \
                    \n \
                    \n - [ ] I, the submitting author, confirm that I have read the note above. \
                    \n 5. ♻️ Update the respective files in [your source repository](payload['repository_url']) with the files you just downloaded and inform the screener. \
                    \n ### For the technical screener \
                    \n Once the submitting author has updated the repository with the `paper.md` and `paper.bib`, please confirm that the PDF successfully builds using the `@roboneuro generate pdf`. \n \
                    \n :warning: However, DO NOT issue  `@roboneuro build extended pdf` command after the submitting author has updated the `paper.md` and `paper.bib`."
            gh_create_comment(github_client, payload['review_repository'],payload['issue_id'],comment)
        except subprocess.CalledProcessError as e:
            gh_template_respond(github_client,"failure",payload['task_title'], payload['review_repository'],payload['issue_id'],task_id,payload['comment_id'], f"{e.output}")
            self.update_state(state=states.FAILURE, meta={'message': e.output})
    else: 
        gh_template_respond(github_client,"failure",payload['task_title'], payload['review_repository'],payload['issue_id'],task_id,payload['comment_id'], f"{res['message']}")
        self.update_state(state=states.FAILURE, meta={'message': res['message']})

