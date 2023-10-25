import os
import re
import pytz
import datetime
import json
import yaml
import git

# Name of the GitHub organization where repositories 
# will be forked into for production. Editorial bot 
# must be authorized for this organization.
GH_ORGANIZATION = "roboneurolibre"

def isNotBlank(myString):
    return bool(myString and myString.strip())

def gh_response_template(task_name,task_id,issue_id,comment_id,message="",collapse=True):
    """
    Please see the docstring of the gh_template_response.
    Convention follows Celery task states.
    """
    tz = pytz.timezone('US/Pacific')
    now = datetime.datetime.now(tz)
    cur_time = now.strftime('%Y-%m-%d %H:%M:%S %Z')
    
    if isNotBlank(message):
        if collapse:
            message = f"\n <details><summary> :information_source: See details </summary><pre><code>{message}</code></pre></details>"
        else:
            message = f"\n {message}"
    else: 
        message = str()

    response_template = dict(
                pending = f"&#9899;  **{task_name}** \n ---------------------------- \n  **Status:** Waiting for task assignment \n **Last updated:** {cur_time} \n {message} \n :recycle: [Refresh](https://github.com/neurolibre/neurolibre-reviews/issues/{issue_id}#issuecomment-{comment_id})",
                received = f"&#9898;  **{task_name}** \n ---------------------------- \n  **Status:** Assigned to task `{task_id[0:8]}` \n **Last updated:** {cur_time} \n {message} \n :recycle: [Refresh](https://github.com/neurolibre/neurolibre-reviews/issues/{issue_id}#issuecomment-{comment_id})",
                started = f"&#128992;  **{task_name}** \n ---------------------------- \n  **Status:** In progress `{task_id[0:8]}` \n **Last updated:** {cur_time} \n {message} \n :recycle: [Refresh](https://github.com/neurolibre/neurolibre-reviews/issues/{issue_id}#issuecomment-{comment_id})",
                success = f"&#128994;  **{task_name}** \n ---------------------------- \n  **Status:** Success `{task_id[0:8]}` \n **Last updated:** {cur_time} \n {message}",
                failure = f"&#128308;  **{task_name}** \n ---------------------------- \n  **Status:** Failed `{task_id[0:8]}` \n **Last updated:** {cur_time} \n {message}",
                exists = f"&#128995; **{task_name}** \n ---------------------------- \n  **Status:** Already exists `{task_id[0:8]}` \n **Last updated:** {cur_time} \n {message}")
    return response_template

def gh_filter(input_str):
    """
    Returns repository name in owner/repository_name format
    """
    github_url_pattern = r'^https?://github\.com/([^/]+)/([^/]+)'
    match = re.match(github_url_pattern, input_str)
    if match:
        owner = match.group(1)
        repo_name = match.group(2)
        return f"{owner}/{repo_name}"
    else:
        return input_str

def gh_forkify_name(input_str):
    """
    Repository name in owner/repo format
    after the repository is forked into the GH_ORGANIZATION.
    
    Does not perform fork operation, string manipulation only. 

    E.g., agahkarakuzu/template --> roboneuro/template.
    """
    source_name = gh_filter(input_str)
    parts = source_name.split("/", 1)
    fork_name = GH_ORGANIZATION + "/" + parts[1]
    return fork_name

def gh_create_comment(github_client,issue_repo,issue_id,comment_body):
    """
    To create a new comment under an existing GitHub issue.
    """
    repo = github_client.get_repo(gh_filter(issue_repo))
    issue = repo.get_issue(number=issue_id)
    commit_comment = issue.create_comment(comment_body)
    return commit_comment.id

def gh_update_comment(github_client, issue_repo,issue_id,comment_id,comment_body):
    """
    Update an existing GitHub issue comment. 
    """
    repo = github_client.get_repo(gh_filter(issue_repo))
    issue = repo.get_issue(issue_id)
    comment = issue.get_comment(comment_id)
    comment.edit(comment_body)

def gh_template_respond(github_client,phase,task_name,repo,issue_id,task_id="",comment_id="", message="",collapsable=True):
    """
    This function is quite practical to connect a GitHub issue
    comment to a running celery background task. 

    phase
        - pending (icon: white circle)
        If the phase is pending, this will create a new issue comment,
        waiting to be updated when a Celery task ID is generated for 
        the respective task that will update its content.
        
        Different than the others, this phase returns the comment ID 
        to be used for updates.

        - received (icon: black circle)
        If the phase is received, the respective issue comment will 
        be updated to include the celery task ID.

        - started (icon: orange circle)
        Use this phase i) to send the first update from the task process
        to inform the user that the process has started and ii) to send 
        following updates at regular (or task specific) intervals.

        - success (icon: green circle)
        Use this phase when task execution has reached the desired state.

        - failure (icon: green circle)
        Use this phase when the task execution is failed.

    task_name
        This will be displayed as the title of the comment associated with 
        the task.
    
    repo
        Repository where the issue exists (e.g., neurolibre-reviews)
    
    issue_id
        Unique ID of the issue in the repo.
    
    task_id (optional)
        Celery task ID.
    
    comment_id (optional)
        Github issue comment ID required to update an existing issue.
    
    message (optional)
        To display custom message/logs in a collapsable line.
    """
    template = gh_response_template(task_name,task_id,issue_id,comment_id,message=message,collapse=collapsable)
    if phase == "pending":
        # This one adds a new comment, returns comment_id
        return gh_create_comment(github_client,repo,issue_id,template['pending'])
    else:
        # This one updates comment, returns None
        return gh_update_comment(github_client,repo,issue_id,comment_id,template[phase])

def gh_get_project_name(github_client,target_repo):
    """
    Read the project name from repo2data file manifest JSON
    file that is required to be located under the binder 
    folder as required by neurolibre.
    """
    repo = github_client.get_repo(gh_filter(target_repo))
    # This is a requirement
    contents = repo.get_contents("binder/data_requirement.json")
    data = json.loads(contents.decoded_content)
    return data['projectName']

def gh_fork_repository(github_client,source_repo):
    """
    Fork repository into the GitHub organization
    where the final version of preprint repositories
    will be forked into.
    """
    repo_to_fork = github_client.get_repo(gh_filter(source_repo))
    target_org = github_client.get_organization(GH_ORGANIZATION)
    forked_repo = target_org.create_fork(repo_to_fork)
    return forked_repo

def gh_get_file_content(github_client,repo,file_path):
    """
    Generic helper function to read (raw) file content from
    a github repository.
    """
    repo = github_client.get_repo(gh_filter(repo))
    try:
        file_content = repo.get_contents(file_path).decoded_content.decode()
    except Exception as e:
        print(f"Error retrieving file content: {str(e)}")
        return ""
    return file_content

def gh_update_file_content(github_client,repo,file_path,new_content,commit_message):
    """
    Generic helper function to update (existing) file content from
    a github repository.
    """
    repo = github_client.get_repo(gh_filter(repo))
    try:
        # Retrieve existing file content
        file = repo.get_contents(file_path)
        # Update the file on GitHub
        repo.update_file(file.path, commit_message, new_content, file.sha)
        return {"status": True, "message": "Success"}
    except Exception as e:
        return {"status": False, "message": str(e)}

def gh_get_jb_config(github_client,repo):
    """
    Get Jupyter Book configuration YAML file content 
    that is under the content directory as required by
    the neurolibre repository structure.
    """
    file_content = gh_get_file_content(github_client,repo,"content/_config.yml")
    if file_content:
        yaml_data = yaml.safe_load(file_content)
    else:
        yaml_data = {}
    return yaml_data

def gh_update_jb_config(github_client,repo,content):
    """
    Update the content of the Jupyter Book configuration YAML 
    that is under the content directory as required by
    the neurolibre repository structure.
    """
    updated_config = yaml.dump(content)
    response = gh_update_file_content(github_client,repo,"content/_config.yml",updated_config,":robot: [Automated] JB configuration update")
    return response

def gh_get_jb_toc(github_client,repo):
    """
    Get Jupyter Book Table Of Contents (TOC) YAML content 
    that is under the content directory as required by
    the neurolibre repository structure.
    """
    file_content = gh_get_file_content(github_client,repo,"content/_toc.yml")
    if file_content:
        yaml_data = yaml.safe_load(file_content)
    else:
        yaml_data = {}
    return yaml_data

def gh_update_jb_toc(github_client,repo,content):
    """
    Update the Jupyter Book Table Of Contents (TOC) YAML content 
    that is under the content directory as required by
    the neurolibre repository structure.
    """
    updated_config = yaml.dump(content)
    response = gh_update_file_content(github_client,repo,"content/_toc.yml",updated_config,":robot: [Automated] JB TOC update")
    return response

def gh_get_paper_markdown(github_client,repo):
    """
    Get paper.md content from the root of the target repository
    """
    file_content = gh_get_file_content(github_client,repo,"paper.md")
    return file_content

def gh_read_from_issue_body(github_client,issue_repo,issue_id,tag):
    """
    Issue body of the reviews has markers around review entries
    such as title, data-archive etc. to identify the respective 
    value. For NeuroLibre reviews, these "tags" are: 
        - data-archive
        - repository-archive
        - book-archive
        - docker-archive
        - book-exec-url
        - target-repository
        - editor
        - version
        - branch

    Returns None if:
        - a requested tag does not exist 
        - the value is Pending
    """
    repo = github_client.get_repo(gh_filter(issue_repo))
    issue = repo.get_issue(issue_id)
    issue_body = issue.body
    # OpenJournals convention.
    start_marker = f"<!--{tag}-->"
    end_marker = f"<!--end-{tag}-->"
    start_index = issue_body.find(start_marker)
    end_index = issue_body.find(end_marker)
    if start_index !=-1 and end_index !=-1:
        extracted_text = issue_body[start_index+len(start_marker):end_index].strip()
        if extracted_text == "Pending":
            extracted_text = None
    else:
        extracted_text = None
    return extracted_text


def get_default_branch(github_client,repository):
    repo = github_client.get_repo(gh_filter(repository))
    default_branch = repo.default_branch
    return default_branch

def gh_clone_repository(repo_url, target_path, depth=1):
    """
    Shallow clones a GitHub repository to the specified target path with the given depth.

    Parameters:
    - repo_url (str): The URL of the GitHub repository.
    - target_path (str): The target directory where the repository will be cloned.
    - depth (int): The depth of the shallow clone (default: 1).

    Returns:
    - None
    """
    # Create target directory if it doesn't exist
    # If the directory already exists, will throw an error.
    if not os.path.exists(target_path):
        os.makedirs(target_path)

    # Clone the repository with the specified depth
    git.Repo.clone_from(repo_url, target_path, depth=depth)
