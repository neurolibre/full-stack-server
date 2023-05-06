import os
import glob
import time
import git
from flask import abort

# GLOBAL VARIABLES
BOOK_PATHS = "/DATA/book-artifacts/*/*/*/*.tar.gz"
BOOK_URL = "https://preview.neurolibre.org/book-artifacts"
DOCKER_REGISTRY = "https://binder-registry.conp.cloud"

def load_all(globpath=BOOK_PATHS):
    book_collection = []
    paths = glob.glob(globpath)
    for path in paths:
        curr_dir = path.replace(".tar.gz", "")
        path_list = curr_dir.split("/")
        commit_hash = path_list[-1]
        repo = path_list[-2]
        provider = path_list[-3]
        user = path_list[-4]
        nb_list = []
        for (dirpath, dirnames, filenames) in os.walk(curr_dir + "/_build/jupyter_execute"):
            for input_file in filenames:
                if input_file.split(".")[-1] == "ipynb":
                    nb_list += [os.path.join(dirpath, input_file).replace("/DATA/book-artifacts", BOOK_URL)]
        nb_list = sorted(nb_list)
        book_dict = {"book_url": BOOK_URL + f"/{user}/{provider}/{repo}/{commit_hash}/_build/html/"
                     , "book_build_logs": BOOK_URL + f"/{user}/{provider}/{repo}/{commit_hash}/book-build.log"
                     , "download_link": BOOK_URL + path.replace("/DATA/book-artifacts", "")
                     , "notebook_list": nb_list
                     , "repo_link": f"https://{provider}/{user}/{repo}"
                     , "user_name": user
                     , "repo_name": repo
                     , "provider_name": provider
                     , "commit_hash": commit_hash
                     , "time_added": time.ctime(os.path.getctime(path))}
        book_collection += [book_dict]
    return book_collection

def book_get_by_params(user_name=None, commit_hash=None, repo_name=None):
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

def get_owner_repo_provider(repo_url):
    """
    Helper function to return owner/repo 
    and a provider name (as abbreviated by BinderHub)
    """
    repo = repo_url.split("/")[-1]
    owner = repo_url.split("/")[-2]
    provider = repo_url.split("/")[-3]
    if provider == "github.com":
        provider = "gh"
    elif provider == "gitlab.com":
        provider = "gl"
    return [owner,repo,provider]

def format_commit_hash(repo_url,commit_hash):
    """
    Returns the latest commit if HEAD (default endpoint value)
    Returns the hash itself otherwise.
    """
    if commit_hash == "HEAD":
        refs = git.cmd.Git().ls_remote(repo_url).split("\n")
        for ref in refs:
            if ref.split('\t')[1] == "HEAD":
                commit_hash = ref.split('\t')[0]
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
    [repo, owner, provider] = get_owner_repo_provider(repo_url)
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
        by the client. The last tree arguments are 
    
    """
    # Parse url to process
    [repo, owner, provider] = get_owner_repo_provider(repo_url)

    if provider not in ["gh","gl"]:
        abort(400, "Unrecognized repository provider.") 

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

    # Get the url to post build rquest and connect to eventstream.
    binderhub_request = get_binder_build_url(binderName, domainName, repo, owner, provider, commit_hash)

    return binderhub_request
