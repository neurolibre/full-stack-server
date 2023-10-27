import os
import sys
import requests
import json
from common import *
from dotenv import load_dotenv
import re
from github import Github
from github_client import gh_read_from_issue_body 
import csv
import subprocess
import nbformat
import re
from bs4 import BeautifulSoup
import shutil
import markdown
import markdownify

load_dotenv()

"""
Helper functions for the tasks 
performed by the preprint (production server).
"""

def zenodo_create_bucket(title, archive_type, creators, repository_url, issue_id):
    
    [owner,repo,provider] =  get_owner_repo_provider(repository_url,provider_full_name=True)

    # ASSUMPTION 
    # Fork exists and has the same name.
    fork_url = f"https://{provider}/roboneurolibre/{repo}"

    ZENODO_TOKEN = os.getenv('ZENODO_API')
    params = {'access_token': ZENODO_TOKEN}
    # headers = {"Content-Type": "application/json",
    #                 "Authorization": "Bearer {}".format(ZENODO_TOKEN)}
    
    # WANING: 
    # FOR NOW assuming that HEAD corresponds to the latest successful
    # book build. That may not be the case. Requires better 
    # data handling or extra functionality to retrieve the latest successful
    # book commit.
    commit_user = format_commit_hash(repository_url,"HEAD")
    commit_fork = format_commit_hash(fork_url,"HEAD")

    libre_text = f"<a href=\"{fork_url}/commit/{commit_fork}\"> reference repository/commit by roboneuro</a>"
    user_text = f"<a href=\"{repository_url}/commit/{commit_user}\">latest change by the author</a>"
    review_text = f"<p>For details, please visit the corresponding <a href=\"https://github.com/neurolibre/neurolibre-reviews/issues/{issue_id}\">NeuroLibre technical screening.</a></p>"
    sign_text = "\n<p><strong><a href=\"https://neurolibre.org\" target=\"NeuroLibre\">https://neurolibre.org</a></strong></p>"

    data = {}
    data["metadata"] = {}
    data["metadata"]["title"] = title
    data["metadata"]["creators"] = creators
    data["metadata"]["keywords"] = ["canadian-open-neuroscience-platform","neurolibre"]
    # (A) NeuroLibre artifact is a part of (isPartOf) the NeuroLibre preprint (B 10.55458/NeuroLibre.issue_id)
    data["metadata"]["related_identifiers"] = [{"relation": "isPartOf","identifier": f"10.55458/neurolibre.{issue_id:05d}","resource_type": "publication-preprint"}]
    data["metadata"]["contributors"] = [{'name':'NeuroLibre, Admin', 'affiliation': 'NeuroLibre', 'type': 'ContactPerson' }]

    if (archive_type == 'book'):
        data["metadata"]["upload_type"] = "other"
        data["metadata"]["description"] = f"NeuroLibre JupyterBook built at this {libre_text}, based on the {user_text}. {review_text} {sign_text}"
    elif (archive_type == 'data'):
        data["metadata"]["upload_type"] = "dataset"
        # TODO: USE OpenAI API here to explain data.
        data["metadata"]["description"] = f"Dataset provided for NeuroLibre preprint.\n Author repo: {repository_url} \nNeuroLibre fork:{fork_url} {review_text}  {sign_text}"
    elif (archive_type == 'repository'):
        data["metadata"]["upload_type"] = "software"
        data["metadata"]["description"] = f"GitHub archive of the {libre_text}, based on the {user_text}. {review_text} {sign_text}"
    elif (archive_type == 'docker'):
        data["metadata"]["upload_type"] = "software"
        data["metadata"]["description"] = f"Docker image built from the {libre_text}, based on the {user_text}, using repo2docker (through BinderHub). <br> To run locally: <ol> <li><pre><code class=\"language-bash\">docker load < DockerImage_10.55458_NeuroLibre_{issue_id:05d}_{commit_fork[0:6]}.tar.gz</code><pre></li><li><pre><code class=\"language-bash\">docker run -it --rm -p 8888:8888 DOCKER_IMAGE_ID jupyter lab --ip 0.0.0.0</code></pre> </li></ol> <p><strong>by replacing <code>DOCKER_IMAGE_ID</code> above with the respective ID of the Docker image loaded from the zip file.</strong></p> {review_text} {sign_text}"

    # Make an empty deposit to create the bucket 
    r = requests.post("https://zenodo.org/api/deposit/depositions",
                params=params,
                json=data)
    
    print(f"Error: {r.status_code} - {r.text}")
    # response_dict = json.loads(r.text)

    # for i in response_dict:
    #     print("key: ", i, "val: ", response_dict[i])

    if not r:
        return {"reason":"404: Cannot create " + archive_type + " bucket.", "commit_hash":commit_fork, "repo_url":fork_url}
    else:
        return r.json()

def zenodo_delete_bucket(remove_link):
    ZENODO_TOKEN = os.getenv('ZENODO_API')
    headers = {"Content-Type": "application/json", "Authorization": "Bearer {}".format(ZENODO_TOKEN)}
    response = requests.delete(remove_link, headers=headers)
    return response

def execute_subprocess(command):
    """
    To asynchronously execute system-levels using celery
    simple calls such as os.system will not work.

    This helper function is to issue system-level command executions 
    using celery.
    """
    # This will be called by Celery, subprocess must be handled properly
    # os.system will not work.
    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        # Capture the output stream
        output = process.communicate()[0]
        # Wait for the subprocess to complete and return the return code of the process
        ret = process.wait()
        if ret == 0:
            status = True
        else:
            status = False
    except subprocess.CalledProcessError as e:
        # If there's a problem with issuing the subprocess.
        output = e.output
        status = False

    return {"status": status, "message": output}

def docker_login():
    uname = os.getenv('DOCKER_USERNAME')
    pswd = os.getenv('DOCKER_PASSWORD')
    command = ["docker", "login", DOCKER_REGISTRY, "--username", uname, "--password-stdin"]
    try:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        output = process.communicate(input=pswd.encode('utf-8'))[0]
        ret = process.wait()
        if ret == 0:
            status = True
        else:
            status = False
    except subprocess.CalledProcessError as e:
        # If there's a problem with issuing the subprocess.
        output = e.output
        status = False

    return {"status": status, "message": output}

def docker_logout():
    command = ["docker", "logout", DOCKER_REGISTRY]
    result  = execute_subprocess(command)
    return result

def docker_pull(image):
    command = ["docker", "pull", image]
    result  = execute_subprocess(command)
    return result

def docker_save(image,issue_id,commit_fork):
    record_name = item_to_record_name("docker")
    save_name = os.path.join(get_archive_dir(issue_id),f"{record_name}_10.55458_NeuroLibre_{issue_id:05d}_{commit_fork[0:6]}.tar.gz")
    try:
        save_process = subprocess.Popen(['docker', 'save', image], stdout=subprocess.PIPE)
        gzip_process = subprocess.Popen(['gzip', '-c'], stdin=save_process.stdout, stdout=open(save_name, 'wb'))
        # Wait for the gzip process to complete
        ret = gzip_process.wait()
        if ret == 0:
            status = True
            output = "Success"
        else:
            status = False
            output = "Fail"
    except subprocess.CalledProcessError as e:
        # If there's a problem with issuing the subprocess.
        output = e.output
        status = False
    return {"status": status, "message": output}, save_name

def get_archive_dir(issue_id):
    path = f"/DATA/zenodo/{issue_id:05d}"
    if not os.path.exists(path):
        os.makedirs(path)
    return path

def get_deposit_dir(issue_id):
    path = f"/DATA/zenodo_records/{issue_id:05d}"
    if not os.path.exists(path):
        os.makedirs(path)
    return path
    # docker rmi $(docker images 'busybox' -a -q)

def zenodo_get_status(issue_id):

    zenodo_dir = f"/DATA/zenodo_records/{issue_id:05d}"

    # Create directory if does not exists.
    if not os.path.exists(zenodo_dir):
        os.makedirs(zenodo_dir)

    file_list = [f for f in os.listdir(zenodo_dir) if os.path.isfile(os.path.join(zenodo_dir,f))]
    res = ','.join(file_list)

    GH_BOT=os.getenv('GH_BOT')
    github_client = Github(GH_BOT)

    data_archive_exists = gh_read_from_issue_body(github_client,"neurolibre/neurolibre-reviews",issue_id,"data-archive")

    regex_repository_upload = re.compile(r"(zenodo_uploaded_repository)(.*?)(?=.json)")
    regex_data_upload = re.compile(r"(zenodo_uploaded_data)(.*?)(?=.json)")
    regex_book_upload = re.compile(r"(zenodo_uploaded_book)(.*?)(?=.json)")
    regex_docker_upload = re.compile(r"(zenodo_uploaded_docker)(.*?)(?=.json)")
    regex_deposit = re.compile(r"(zenodo_deposit)(.*?)(?=.json)")
    regex_publish = re.compile(r"(zenodo_published)(.*?)(?=.json)")
    hash_regex = re.compile(r"_(?!.*_)(.*)")

    if data_archive_exists:
        zenodo_regexs = [regex_repository_upload, regex_book_upload, regex_docker_upload]
        types = ['Repository', 'Book', 'Docker']
    else:
        zenodo_regexs = [regex_repository_upload, regex_data_upload, regex_book_upload, regex_docker_upload]
        types = ['Repository', 'Data', 'Book', 'Docker']

    rsp = []

    if not regex_deposit.search(res):
        rsp.append("<h3>Deposit</h3>:red_square: <b>Zenodo deposit records have not been created yet.</b>")
    else:
        rsp.append("<h3>Deposit</h3>:green_square: Zenodo deposit records are found.")

    rsp.append("<h3>Upload</h3><ul>")
    for cur_regex, idx in zip(zenodo_regexs, range(len(zenodo_regexs))):
        print(cur_regex)
        print(idx)
        if not cur_regex.search(res):
            rsp.append("<li>:red_circle: <b>{}</b></li>".format(types[idx] + " archive is missing"))
        else:
            tmp = cur_regex.search(res)
            json_file = tmp.string[tmp.span()[0]:tmp.span()[1]] + '.json'
            print(tmp)
            # Display file size for uploaded items, so it is informative.
            with open(os.path.join(zenodo_dir,json_file), 'r') as f:
                # Load the JSON data
                cur_record = json.load(f)
            #cur_record = json.loads(response.text)
            # Display MB or GB depending on the size.
            print(cur_record['size'])
            size = round((cur_record['size'] / 1e6),2)
            if size > 999:
                size = "{:.2f} GB".format(cur_record['size'] / 1e9)
            else:
                size = "{:.2f} MB".format(size)
            # Format
            rsp.append("<li>:green_circle: {} archive <ul><li><code>{}</code> <code>{}</code></li></ul></li>".format(types[idx], size, json_file))
    rsp.append("</ul><h3>Publish</h3>")

    if not regex_publish.search(res):
        rsp.append(":small_red_triangle_down: <b>Zenodo DOIs have not been published yet.</b>")
    else:
        rsp.append(":white_check_mark: Zenodo DOIs are published.")

    return ''.join(rsp)

def item_to_record_name(item):
    dict_map = {"data":"Dataset",
                "repository":"GitHubRepo",
                "docker":"DockerImage",
                "book":"JupyterBook"}
    if item in dict_map.keys():
        return dict_map[item]
    else: 
        return None

def zenodo_upload_item(upload_file,bucket_url,issue_id,commit_fork,item_name):
    ZENODO_TOKEN = os.getenv('ZENODO_API')
    params = {'access_token': ZENODO_TOKEN}
    record_name = item_to_record_name(item_name)
    extension = "zip"

    if item_name == "docker":
        extension = "tar.gz"

    if record_name:
        try:
            with open(upload_file, "rb") as fp:
                r = requests.put(f"{bucket_url}/{record_name}_10.55458_NeuroLibre_{issue_id:05d}_{commit_fork[0:6]}.{extension}",
                                        params=params,
                                        data=fp)
        except requests.exceptions.RequestException as e:
            r = str(e)
    else:

        r = None

    return r


def find_resource_idx(lst, repository_url):
    """
    Helper function for get_resource_lookup.
    """
    tmp = [index for index, item in enumerate(lst) if repository_url in item[0]]
    if tmp:
        return tmp[0]
    else:
        return None

def parse_tsv_content(content):
    """
    Helper function for get_resource_lookup.
    """
    # Create a CSV reader object
    reader = csv.reader(content.splitlines(), delimiter='\t')
    # Skip the header row
    next(reader)
    # Create a list to store the parsed data
    parsed_data = []
    # Iterate over each row and add it to the parsed_data list
    for row in reader:
        parsed_data.append(row)
    
    return parsed_data

def get_test_book_build(preview_server,verify_ssl,commit_hash):
    """
    Call test server API to see if a book has been built at a
    specific commit hash. To be used by the preprint server before
    starting the production phase.
    """
    url = f"{preview_server}/api/book"
    headers = {'Content-Type': 'application/json'}
    API_USER = os.getenv('TEST_API_USER')
    API_PASS = os.getenv('TEST_API_PASS')
    auth = (API_USER, API_PASS)
    params = {"commit_hash": commit_hash}
    # Send GET request
    response = requests.get(url, headers=headers, auth=auth, params=params, verify=verify_ssl)
    if response.status_code == 200:
        return {'status': True, 'book_url': json.loads(response.text)[0]['book_url']}
    else:
        return {'status': False, 'book_url': None}

def get_resource_lookup(preview_server,verify_ssl,repository_address):
    """
    For a given repository address, returns a dictionary 
    that contains the following fields:
        - "date","repository_url","docker_image","project_name","data_url","data_doi"
    IF there's a successful book build exists for the respective inquiry.

    Returns None otherwise.

    The lookup_table.tsv exists on the preview server.

    Ideally, this should be dealt with using a proper database instead of a tsv file.
    """
    
    url = f"{preview_server}/book-artifacts/lookup_table.tsv"
    headers = {'Content-Type': 'application/json'}
    API_USER = os.getenv('TEST_API_USER')
    API_PASS = os.getenv('TEST_API_PASS')
    auth = (API_USER, API_PASS)

    # Send GET request
    response = requests.get(url, headers=headers, auth=auth, verify=verify_ssl)
    
    # Process response
    if response.ok:
        # Get content body
        content = response.content.decode('utf-8')
        # Parse content
        parsed_data = parse_tsv_content(content)
        # Get string that contains the repo_url
        idx = find_resource_idx(parsed_data,repository_address)

        if idx:
            # Convert to list
            values = parsed_data[idx][0].split(",")
            # Convert to dict 
            # The last two keys are not reliable (that may contain comma that is not separating tsv column)
            # also due to subpar documentation issue with repo2data.
            keys = ["date","repository_url","docker_image","project_name","data_url","data_doi"]
            lut = dict(zip(keys, values))
        else: 
            lut = None
    else:
        
        lut = None
    
    return lut

def zenodo_publish(issue_id):
    ZENODO_TOKEN = os.getenv('ZENODO_API')
    params = {'access_token': ZENODO_TOKEN}
    # Read json record of the deposit
    message = []

    upload_status = zenodo_confirm_status(issue_id,"uploaded")

    if upload_status[1] == "no-record-found":
        return "no-record-found"

    if upload_status[0]:
        zenodo_record = get_zenodo_deposit(issue_id)
        # We need self links from each record to publish.
        for item in zenodo_record.keys():
            publish_link = zenodo_record[item]['links']['publish']
            message.append(f"\n :ice_cube: {item_to_record_name(item)} publish status:")
            r = requests.post(publish_link,params=params)
            response = r.json()
            if r.status_code==202: 
                message.append(f"\n :confetti_ball: <a href=\"{response['doi_url']}\"><img src=\"{response['links']['badge']}\"></a>")
                tmp = f"zenodo_published_{item}_NeuroLibre_{issue_id:05d}.json"
                log_file = os.path.join(get_deposit_dir(issue_id), tmp)
                with open(log_file, 'w') as outfile:
                    json.dump(r.json(), outfile)
            else:
                message.append(f"\n <details><summary> :wilted_flower: Could not publish {item_to_record_name(item)} </summary><pre><code>{r.json()}</code></pre></details>")
    else:
        message.append(f"\n :neutral_face: {upload_status[1]} all archives are uploaded for the resources listed in the deposit record. Please ask <code>roboneuro zenodo status</code> and upload the missing  archives by <code>roboneuro zenodo upload <item></code>.")

    return message

def zenodo_confirm_status(issue_id,status_type):
    """
    Helper function to confirm the uploaded or published status
    for all zenodo archive types declares in a deposit file for
    a given issue id.

    status_type can be:
        - uploaded
        - published
    """

    zenodo_record = get_zenodo_deposit(issue_id)

    if not zenodo_record:
        return [False,"no-record-found"]
    else:
        bool_array = []
        for item in zenodo_record.keys():
            if status_type == "published":
                # Does not append commit hash
                tmp = glob.glob(os.path.join(get_deposit_dir(issue_id),f"zenodo_{status_type}_{item}_NeuroLibre_{issue_id:05d}.json"))
            elif status_type == "uploaded":
                # Appends commit hash
                tmp = glob.glob(os.path.join(get_deposit_dir(issue_id),f"zenodo_{status_type}_{item}_NeuroLibre_{issue_id:05d}_*.json"))

            if tmp:
                bool_array.append(True)
            else:
                bool_array.append(False)

        all_true = all(bool_array)
        all_false = not any(bool_array)

        if all_true:
           return [True,"All"]
        elif all_false:
           return [False,"None"]
        elif not (all_true or all_false):
           return [False,"Some"]

def get_zenodo_deposit(issue_id):
    fname = f"zenodo_deposit_NeuroLibre_{issue_id:05d}.json"
    local_file = os.path.join(get_deposit_dir(issue_id), fname)
    if not os.path.exists(local_file):
        zenodo_record = None
    else:
        with open(local_file, 'r') as f:
            zenodo_record = json.load(f)
    return zenodo_record

def zenodo_collect_dois(issue_id):
    zenodo_record = get_zenodo_deposit(issue_id)
    collect = {}
    for item in zenodo_record.keys():
        tmp = glob.glob(os.path.join(get_deposit_dir(issue_id),f"zenodo_published_{item}_NeuroLibre_{issue_id:05d}.json"))
        with open(tmp[0], 'r') as f:
            tmp_record = json.load(f)
        collect[item] = tmp_record['doi']
    return collect

# Function to extract citations from a cell's source code
def extract_citations(text, pattern):
    # Find all instances of {cite:*}`some-text`
    matches = re.findall(pattern, text)
    return matches

# Function to remove HTML tags from Markdown content
def remove_html_tags(markdown):
    soup = BeautifulSoup(markdown, "html.parser")
    return soup.get_text()

def extract_paragraphs_with_citations(notebook):
    paragraphs_with_citations = []
    current_paragraph = ''
    current_section = ''
    # Process each cell in the notebook
    for cell in notebook['cells']:
        if cell['cell_type'] == 'markdown':
            # Extract citations based on the appropriate pattern
            #matches = extract_citations(cell['source'], r'\[@([^[\]]*)\]')
            # If there are citations, store the current paragraph and its section
            #if matches:
            if current_paragraph:
                paragraphs_with_citations.append({'section': current_section, 'paragraph': current_paragraph})
                current_paragraph = ''
            current_paragraph += cell['source']
            current_section = cell.get('metadata', {}).get('section', '')
    # Add the last paragraph if it has citations
    if current_paragraph:
        paragraphs_with_citations.append({'section': current_section, 'paragraph': current_paragraph})
    return paragraphs_with_citations

def substitute_cite_commands(input_folder="content"):
    # Process each file in the input folder
    for file_name in os.listdir(input_folder):
        file_path = os.path.join(input_folder, file_name)
        if file_name.endswith('.ipynb'):
            # Read Jupyter notebook(s)
            notebook = nbformat.read(file_path, as_version=nbformat.NO_CONVERT)
            notebook.cells = [cell for cell in notebook.cells if cell.cell_type != "code"]
            # Process each cell in the notebook
            for cell in notebook['cells']:
                if cell['cell_type'] == 'markdown':
                    # Deal with (Author et al. YYYY) and (Someone et al. YYYY, Someone-else et al. ZZZZ)
                    matches = extract_citations(cell['source'], r'\{cite:p\}`([^`]*)`')
                    if matches:
                        # Embed citations in the cell's source code
                        for match in matches:
                            # Split the citations by comma and format them accordingly
                            citations = match.split(',')
                            try:
                                formatted_citations = '; '.join([f'@{citation.strip()}' for citation in citations])
                            except:
                                pass

                            # Replace the original pattern with the formatted citations
                            cell['source'] = re.sub(r'\{cite:p\}`([^`]*)`', f'[{formatted_citations}]', cell['source'], count=1)
                # Deal with Author et al. (YYYY)
                    matches = extract_citations(cell['source'], r'\{cite:t\}`([^`]*)`')
                    if matches:
                        # Embed citations in the cell's source code
                        for match in matches:
                            # Split the citations by comma and format them accordingly
                            citations = match.split(',')
                            try:
                                formatted_citations = '; '.join([f'@{citation.strip()}' for citation in citations])
                            except:
                                pass
                            # Replace the original pattern with the formatted citations
                            cell['source'] = re.sub(rf'\{{cite:t\}}`{match}`', f'{formatted_citations}', cell['source'], count=1)

            # Export the notebook as Markdown
            filtered_paragraphs = extract_paragraphs_with_citations(notebook)

            markdown_output = ''
            for paragraph_info in filtered_paragraphs:
                if paragraph_info['section']:
                    markdown_output += "\n\n"
                    markdown_output += f"## {paragraph_info['section']}\n\n"

                # Remove admonition, table, figure etc. blocks
                cleaned_paragraph = re.sub(r'```{.*?}*```', '', paragraph_info['paragraph'], flags=re.DOTALL)
                markdown_output += f"{cleaned_paragraph}\n"

            # Get rid of HTML tags
            markdown_output = remove_html_tags(markdown_output)
            return markdown_output

def append_bib_files(file1_path, file2_path, output_path):
    # Read the contents of the first BibTeX file
    with open(file1_path, 'r', encoding='utf-8') as file1:
        content1 = file1.read()
    # Read the contents of the second BibTeX file
    with open(file2_path, 'r', encoding='utf-8') as file2:
        content2 = file2.read()
    # Combine the contents of both files
    combined_content = content1 + '\n' + content2
    # Write the combined content to the output file
    with open(output_path, 'w', encoding='utf-8') as output_file:
        output_file.write(combined_content)

def merge_and_check_bib(target_path):
    """
    For now simply appending one bib to another 
    later on, add duplication check.
    """
    orig_bib = os.path.join(target_path,"paper.bib")
    backup_bib = os.path.join(target_path,"paper_backup.bib")
    # Create a backup for the original markdown.
    shutil.copyfile(orig_bib, backup_bib)
    # Simply merge two bib files.
    # TODO: GET THE DIRECTORY FROM FLASK 
    partial_bib = "/home/ubuntu/full-stack-server/assets/partial.bib"
    append_bib_files(orig_bib, partial_bib, orig_bib)

def create_extended_pdf_sources(target_path, issue_id,repository_url):
    """
    target_path is where repository_url is cloned by the celery worker.
    """
    # This will crawl all the Jupyter Notebooks to collect text that cites
    # articles, then will substitute MyST cite commands with Pandoc directives 
    # recognized by OpenJournals PDF compilers.\
    try: 
        markdown_output = substitute_cite_commands(os.path.join(target_path,"content"))
        orig_paper = os.path.join(target_path,"paper.md")
        backup_paper = os.path.join(target_path,"paper_backup.md")
        # Create a backup for the original markdown.
        shutil.copyfile(orig_paper, backup_paper)
        with open(orig_paper, 'a') as file:
            file.write("\n")
            file.write("\n \\awesomebox[red]{2pt}{\\faExclamationCircle}{red}{\\textbf{NOTE}}")
            file.write(f"\n\n > **_NOTE:_** The following section in this document repeats the narrative content exactly as \
                    found in the [corresponding NeuroLibre Reproducible Preprint (NRP)](https://preprint.neurolibre.org/10.55458/neurolibre.{issue_id:05d}). The content was \
                    automatically incorporated into this PDF using the NeuroLibre publication workflow [@Karakuzu2022-nlwf] to \
                    credit the referenced resources. The submitting author of the preprint has verified and approved the \
                    inclusion of this section through a GitHub pull request made to the [source repository]({repository_url}) from which this document was built. \
                    Please note that the figures and tables have been excluded from this (static) document. **To interactively explore such outputs and re-generate them, please visit the corresponding [NRP](https://preprint.neurolibre.org/10.55458/neurolibre.{issue_id:05d}).** \
                    For more information on integrated research objects (e.g., NRPs) that bundle narrative and executable content for reproducible and transparent publications, \
                    please refer to @Dupre2022-iro. NeuroLibre is sponsored by the Canadian Open Neuroscience Platform (CONP) [@Harding2023-conp].\n\n")
            file.write(markdownify.markdownify(markdown.markdown(markdown_output)))
            file.write("\n\n# References\n\n")
        # Update the bibliography for NeuroLibre entries.
        merge_and_check_bib(target_path)
        return {"status":True, "message":"Extended PDF resources have been created."}
    except Exception as e:
        # In case returning these logs to the user is desired.
        return {"status":False, "message": str(e)}


def nb_to_lab(file_path):
    with open(file_path, 'r') as f:
        content = f.read()
    
    updated_content = re.sub(r'\?urlpath=tree/content/', '?urlpath=lab/tree/content/', content)
    
    with open(file_path, 'w') as f:
        f.write(updated_content)

def enforce_lab_interface(directory_path):
    """
    Rewrite =tree/content with =lab/tree/content
    """
    for root, dirs, files in os.walk(directory_path):
        for file in files:
            if file.endswith('.html'):
                file_path = os.path.join(root, file)
                nb_to_lab(file_path)