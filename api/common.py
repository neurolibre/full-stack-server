import os
import glob
import time
import git
import requests
import json
from flask import abort
from itertools import chain
import yaml
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail, Attachment, FileContent, FileName, FileType, Disposition
from dotenv import load_dotenv
from openai import OpenAI
import humanize
import subprocess
import logging
import psutil
import pytz
import datetime


"""
Helper functions for the tasks 
performed by both servers (preview and preprint).
"""

load_dotenv()

def load_yaml(file):
    with open(file, 'r') as file:
        data = yaml.safe_load(file)
    return data 

common_config  = load_yaml('config/common.yaml')
preview_config  = load_yaml('config/preview.yaml')

JB_ROOT_PATH = f"{common_config['DATA_ROOT_PATH']}/{common_config['JB_ROOT_FOLDER']}"

MYST_ROOT_PATH = f"{common_config['DATA_ROOT_PATH']}/{common_config['MYST_FOLDER']}"

def load_all():
    """
    Get the list of all books (Jupyter Book and MyST) that exist in the server.
    """
    BOOK_PATHS = {
    "jupyter_book": f"{common_config['DATA_ROOT_PATH']}/{common_config['JB_ROOT_FOLDER']}/*/*/*/*.tar.gz",
    "myst": f"{common_config['DATA_ROOT_PATH']}/{common_config['MYST_FOLDER']}/*/*/*.tar.gz"}

    PREVIEW_BOOK_URL = {
        "jupyter_book": f"https://{preview_config['SERVER_SLUG']}.{common_config['SERVER_DOMAIN']}/{common_config['JB_ROOT_FOLDER']}",
        "myst": f"https://{preview_config['SERVER_SLUG']}.{common_config['SERVER_DOMAIN']}/{common_config['MYST_FOLDER']}"}

    book_collection = []
    single_page_path = "/_build/_page/index/jupyter_execute"
    multi_page_path = "/_build/jupyter_execute"

    for format_type, path_pattern in BOOK_PATHS.items():
        paths = glob.glob(path_pattern)
        root_path = JB_ROOT_PATH if format_type == "jupyter_book" else MYST_ROOT_PATH
        preview_url = PREVIEW_BOOK_URL[format_type]  # Get the correct preview URL for this format

        for path in paths:
            curr_dir = path.replace(".tar.gz", "")
            path_list = curr_dir.split("/")
            commit_hash = path_list[-1]
            repo = path_list[-2]
            provider = path_list[-3]
            user = path_list[-4]
            nb_list = []

            # Only look for notebooks in Jupyter Book format
            if format_type == "jupyter_book":
                for (dirpath, dirnames, filenames) in chain(
                    os.walk(curr_dir + multi_page_path),
                    os.walk(curr_dir + single_page_path)
                ):
                    for input_file in filenames:
                        if input_file.split(".")[-1] == "ipynb":
                            nb_list += [os.path.join(dirpath, input_file).replace(root_path, preview_url)]
                nb_list = sorted(nb_list)

                if multi_page_path in dirpath:
                    cur_url = f"{preview_url}/{user}/{provider}/{repo}/{commit_hash}/_build/html/"
                elif single_page_path in dirpath:
                    cur_url = f"{preview_url}/{user}/{provider}/{repo}/{commit_hash}/_build/_page/index/singlehtml/"
            else:  # MyST format
                cur_url = f"{preview_url}/{user}/{provider}/{repo}/{commit_hash}/_build/html/"

            book_dict = {
                "book_url": cur_url,
                "book_build_logs": f"{preview_url}/{user}/{provider}/{repo}/{commit_hash}/book-build.log",
                "download_link": f"{preview_url}{path.replace(root_path, '')}",
                "notebook_list": nb_list,
                "repo_link": f"https://{provider}/{user}/{repo}",
                "user_name": user,
                "repo_name": repo,
                "provider_name": provider,
                "commit_hash": commit_hash,
                "format_type": format_type,
                "time_added": time.ctime(os.path.getctime(path))
            }
            book_collection += [book_dict]
    
    return book_collection

def book_get_by_params(user_name=None, commit_hash=None, repo_name=None):
    """
    Returns a book object if it exists for one or for the intersection
    of multiple parameters passed as an argument to the function.
    Typical use case is with commit_hash.
    """
    books = load_all()
    # Create an empty list for our results
    results = []
    # If we have the hash, return the corresponding book
    if user_name is not None:
        for book in books:
            if book['user_name'] == user_name:
                results.append(book)
    elif commit_hash is not None:
        for book in books:
            if book['commit_hash'] == commit_hash:
                results.append(book)
    elif repo_name is not None:
        for book in books:
            if book['repo_name'] == repo_name:
                results.append(book)
    return results

def get_owner_repo_provider(repo_url,provider_full_name=False):
    """
    Helper function to return owner/repo 
    and a provider name (as abbreviated by BinderHub)
    """
    repo = repo_url.split("/")[-1]
    owner = repo_url.split("/")[-2]
    provider = repo_url.split("/")[-3]
    if provider not in ["github.com","gitlab.com","www.github.com","www.gitlab.com"]:
        abort(400, "Unrecognized repository provider.")
    
    if provider == "www.github.com":
        provider = "github.com"
    if provider == "www.gitlab.com":
        provider = "gitlab.com"

    if not provider_full_name:
        if (provider == "github.com"):
            provider = "gh"
        elif (provider == "gitlab.com"):
            provider = "gl"

    return [owner,repo,provider]

def format_commit_hash(repo_url, commit_hash):
    """
    Returns the latest commit if HEAD (default endpoint value)
    Returns the hash itself otherwise.
    """
    if commit_hash == "HEAD":
        attempt = 0
        max_attempts = 5
        while attempt < max_attempts:
            try:
                refs = git.cmd.Git().ls_remote(repo_url).split("\n")
                for ref in refs:
                    if ref.split('\t')[1] == "HEAD":
                        commit_hash = ref.split('\t')[0]
                break  # Exit the loop if successful
            except Exception as e:
                attempt += 1
                if attempt < max_attempts:
                    time.sleep(10)  # Wait for 10 seconds before retrying
                else:
                    raise e  # Re-raise the exception if all attempts fail
    return commit_hash

def get_binder_build_url(binderName, domainName, repo, owner, provider, commit_hash):
    """
    Simple helper function to return binderhub build request URI.
    """
    return f"https://{binderName}.{domainName}/build/{provider}/{owner}/{repo}.git/{commit_hash}"

def get_lock_filename(repo_url):
    """
    Simple helper function to identify the lock filename.
    """
    [owner, repo, provider] = get_owner_repo_provider(repo_url)
    fname = f"{provider}_{owner}_{repo}.lock"
    return os.path.join(os.getcwd(),'build_locks',fname)

def check_lock_status(lock_filename,build_rate_limit):
    """
    If lock has expired, remove it (unlocked)
    If not expired, return the remaining time in seconds.
    If never existed, inform (not_locked)
    Non-numeric returns are for semantics only. Downstream 
    flow is determined based on numeric or not. 
    """
    if os.path.exists(lock_filename):
    # If lock exists, check its age first.
            lock_age_in_sec = time.time() - os.path.getmtime(lock_filename)
            # If the lock file older than the rate limit, remove.
            if lock_age_in_sec > build_rate_limit*60:
                os.remove(lock_filename)
                return "unlocked"
            else: 
                # Return remaining time in seconds
                return round(build_rate_limit - lock_age_in_sec/60,1)
    else:
        return "not_locked"
    
def run_binder_build_preflight_checks(repo_url,commit_hash,build_rate_limit, binderName, domainName):
    """
        Two arguments repo_url and commit_hash are passed with payload
        by the client. The last tree arguments are from configurations.
    """
    # Parse url to process
    [owner, repo, provider] = get_owner_repo_provider(repo_url)

    # Get lock filename
    lock_filename = get_lock_filename(repo_url)

    # First check on build lock conditions.
    lock_status = check_lock_status(lock_filename,build_rate_limit)

    if isinstance(lock_status, (int, float)):
    # If lock is not expired, deny request and inform the client.
        abort(409, f"Looks like a build is already in progress for {owner}/{repo}. Will be unlocked in {lock_status} minutes. Please try again later or request unlock (reviewers/editors only).")
    else:
        # Create a fresh lock and proceed to build.
        with open(lock_filename, "w") as f:
            f.write("")

    # Get the latest commit hash if HEAD, pass otherwise.
    commit_hash = format_commit_hash(repo_url,commit_hash)

    # Get the url to post build request and connect to eventstream.
    binderhub_request = get_binder_build_url(binderName, domainName, repo, owner, provider, commit_hash)

    return binderhub_request

def get_reports_dir(root_dir):
    """
    Depending on the format of the Jupyter Book (single or multipage),
    the location of the reports vary. This helper function
    checks both possible options.
    """
    multi_page_path = f"{root_dir}/_build/html/reports" 
    single_page_path = f"{root_dir}/_build/_page/index/singlehtml/reports"
    if os.path.exists(multi_page_path) and os.path.isdir(multi_page_path):
        return multi_page_path
    elif os.path.exists(single_page_path) and os.path.isdir(single_page_path):
        return multi_page_path
    else: 
        return None

def book_execution_errored(owner,repo,provider,commit_hash):
    root_dir = f"{JB_ROOT_PATH}/{owner}/{provider}/{repo}/{commit_hash}"
    reports_path = get_reports_dir(root_dir)
    if not reports_path:
        return False
    # When the directory exists, check its contents.
    file_list = None
    file_list = [f for f in os.listdir(reports_path) if os.path.isfile(os.path.join(reports_path,f))]
    if file_list and len(file_list) > 0:
        return True
    else:
        return False

def book_log_collector(owner,repo,provider,commit_hash):
    """
    Retrieve the content of Jupyter Book build logs. 
    The main log (book-build.log) exists both on build success or failure.
    Execution report logs only come to existence if something went wrong 
    while executing the respective notebook.
    """
    logs = []
    root_dir = f"{JB_ROOT_PATH}/{owner}/{provider}/{repo}/{commit_hash}"
    main_log_file = f"{root_dir}/book-build.log"
    if os.path.isfile(main_log_file):
        with open(main_log_file) as f:
            mainlog = [line.rstrip() for line in f]
        mainlog  = "\n".join(mainlog)
        book_log = f"<details><summary> <b>Jupyter Book build log</b> </summary><pre><code>{mainlog}</code></pre></details>"
        logs.append(book_log)
        # Look at the reports directory
        reports_path = get_reports_dir(root_dir)
        if reports_path:
            file_list = [f for f in os.listdir(reports_path) if os.path.isfile(os.path.join(reports_path,f))]
            # Collect each one of these logs
            for file_name in file_list:
                with open(f"{reports_path}/{file_name}") as file:
                    cur_log = [line.rstrip() for line in file]
                cur_log  = "\n".join(cur_log)
                base_name = file_name.split(".")[0]
                msg= f"<details><summary> <b>Execution error log</b> for <code>{base_name}</code> notebook ({base_name}.ipynb) or MyST ({base_name}.md)).</summary><pre><code>{cur_log}</code></pre></details>"
                logs.append(msg)
        msg = "<p>&#128030; After inspecting the logs above, you can interactively debug your notebooks on our <a href=\"https://binder.conp.cloud\">BinderHub server</a>.</p> <p>For guidelines, please see <a href=\"https://docs.neurolibre.org/en/latest/TEST_SUBMISSION.html#debugging-for-long-neurolibre-submission\">the relevant documentation.</a></p>"
        logs.append(msg)
    else: 
        logs.append(f"I could not find any book log for {owner}/{repo} at {commit_hash}")
    logs  = "\n".join(logs)
    return logs

def parse_front_matter(markdown_string):
    """
    Simple function to read front-matter yaml data 
    from markdown files (e.g., paper.md).
    """
    lines = markdown_string.split('\n')
    front_matter_lines = []
    in_front_matter = False

    for line in lines:
        if line.strip() == '---':  # Start or end of front matter
            in_front_matter = not in_front_matter
            continue

        if in_front_matter:
            front_matter_lines.append(line)
        else:
            break

    front_matter = '\n'.join(front_matter_lines)
    return yaml.safe_load(front_matter)

def send_email(to_email, subject, body):
    sg_api_key = os.getenv('SENDGRID_API_KEY')
    sender_email = common_config['SENDER_EMAIL']

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



def send_email_with_html_attachment(to_email, subject, body, attachment_path):
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

    # Add the attachment to the email with MIME type "text/html"
    attachment = Attachment(
        FileContent(data),
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

def remove_first_last_slash(input_string):
    if input_string.startswith('/'):
        input_string = input_string[1:]
    if input_string.endswith('/'):
        input_string = input_string[:-1]
    return input_string

def get_book_target_tail(book_url,commit_hash):
    """
    Based on a book URL returned by the server, get the 
    last parts of its expected local directory.
    If multi-page /_build/html/, if single page, should be /_build/_page/index/singlehtml/
    Remove first and last /. 
    """
    # After the commit hash, the pattern informs whether it is single or multi page.
    format_url = book_url.split(commit_hash)
    book_target = remove_first_last_slash(format_url[1])
    return book_target

def get_gpt_response(prompt):
    client = OpenAI(api_key=os.getenv('OAI_TOKEN'))
    try:
        response = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": "You are a helpful editorial bot for a scientific journal. Your task is to help publish reproducible articles."},
            {"role": "user", "content": prompt}
        ])
        answer = response.choices[0].message.content
    except:
        answer = None

    if answer != None:
        return answer
    else:
        return "GPT is AFK, keep hanging out with roboneuro."

def get_directory_content_summary(path):
    """
    Get the summary of the directory content.
    """
    content = []
    total_size = 0
    for root, dirs, files in os.walk(path):
        for name in files:
            file_path = os.path.join(root, name)
            size = os.path.getsize(file_path)
            total_size += size
            content.append((file_path.replace(path, '').lstrip('/'), humanize.naturalsize(size)))
    return content, humanize.naturalsize(total_size)

def load_json(file_path):
    with open(file_path, 'r') as f:
        return json.load(f)

def github_alert(message, alert_type='note'):
    """
    Generate a GitHub-compatible markdown alert.

    :param message: The message to be displayed in the alert. Can be multi-line.
    :param alert_type: The type of alert. Can be 'note', 'tip', 'important', 'warning', or 'caution'.
    :return: A string containing the formatted GitHub alert.
    """
    valid_types = ['note', 'tip', 'important', 'warning', 'caution']
    alert_type = alert_type.lower()

    if alert_type not in valid_types:
        raise ValueError(f"Invalid alert type. Must be one of {', '.join(valid_types)}.")

    # Split the message into lines and add '> ' to each line
    formatted_message = '\n> '.join(message.split('\n'))

    return f"> [!{alert_type.upper()}]\n> {formatted_message}"

def run_celery_subprocess(command, log_output=True):
    """
    Run a subprocess command, capture its output, and handle potential errors.

    :param command: List containing the command and its arguments
    :param log_output: Boolean to determine if the output should be logged (default: True)
    :return: A tuple containing (return_code, output)
    """
    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
        output, _ = process.communicate()
        return_code = process.wait()

        if log_output:
            logging.info(f"Command: {' '.join(command)}")
            logging.info(f"Output: {output}")
            logging.info(f"Return code: {return_code}")

        return return_code, output

    except subprocess.CalledProcessError as e:
        logging.error(f"Subprocess error: {e}")
        logging.error(f"Command: {' '.join(command)}")
        logging.error(f"Output: {e.output}")
        return e.returncode, e.output

    except Exception as e:
        logging.error(f"Unexpected error: {e}")
        logging.error(f"Command: {' '.join(command)}")
        return -1, str(e)

def get_active_ports(start=3001, end=3099):
    active_ports = []
    for conn in psutil.net_connections(kind='inet'):
        if conn.status == psutil.CONN_LISTEN and start <= conn.laddr.port <= end:
            active_ports.append(conn.laddr.port)
    return active_ports

def close_port(port):
    """
    Find and terminate processes using the specified port
    """
    try:
        logging.info(f"Attempting to close port {port}")
        for conn in psutil.net_connections(kind='inet'):
            if conn.laddr.port == port:
                process = psutil.Process(conn.pid)
                process.terminate()
                logging.info(f"Terminated process {conn.pid} using port {port}")
                # Wait for the process to actually terminate
                process.wait(timeout=5)
                return True
        logging.warning(f"No process found using port {port}")
        return False
    except Exception as e:
        logging.error(f"Error closing port {port}: {e}")
        return False

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

def write_log(owner, repo, log_type, log_content, info_dict=None):
    """
    Write a log file to the logs folder.
    """
    now = get_time()
    log_file_path = f"{common_config['DATA_ROOT_PATH']}/{common_config['LOGS_FOLDER']}/{log_type}/{owner}/{repo}"
    os.makedirs(log_file_path, exist_ok=True)
    log_file_path = f"{log_file_path}/{now}.log"
    
    # Prepare the info_dict content
    if info_dict is None:
        info_dict = {}

    info_dict['created_at'] = get_time()    

    info_content = "\n".join(f"{key}: {value}" for key, value in info_dict.items()) + "\n"
    
    with open(log_file_path, 'a') as log_file:
        # Write the info_dict content first
        log_file.write(info_content)
        # Write the main log content
        log_file.write(log_content)

    # Return the path to the log file for the UI (api/logs/<path:file_path>)
    return f"{log_type}/{owner}/{repo}/{now}.log"