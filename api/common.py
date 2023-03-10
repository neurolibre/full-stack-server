import os
import glob
import time

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