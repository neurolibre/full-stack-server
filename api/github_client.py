import os
import re
import pytz
import datetime
import json
import yaml

# Name of the GitHub organization where repositories 
# will be forked into for production. Editorial bot 
# must be authorized for this organization.
GH_ORGANIZATION = "roboneurolibre"

def isNotBlank(myString):
    return bool(myString and myString.strip())

def gh_response_template(task_name,task_id,message=""):
    tz = pytz.timezone('US/Pacific')
    now = datetime.datetime.now(tz)
    cur_time = now.strftime('%Y-%m-%d %H:%M:%S %Z')
    
    if isNotBlank(message):
        message = f"\n <details><summary> :information_source: See details </summary><pre><code>{message}</code></pre></details>"
    else: 
        message = str()
    
    
    response_template = dict(
                pending = f"&#9899;  **{task_name}** \n ---------------------------- \n  **Status:** Waiting for task assignment \n **Last updated:** {cur_time} \n {message}",
                received = f"&#9898;  **{task_name}** \n ---------------------------- \n  **Status:** Assigned to task `{task_id[0:8]}` \n **Last updated:** {cur_time} \n {message}",
                started = f"&#128992;  **{task_name}** \n ---------------------------- \n  **Status:** In progress `{task_id[0:8]}` \n **Last updated:** {cur_time} \n {message}",
                success = f"&#128994;  **{task_name}** \n ---------------------------- \n  **Status:** Success `{task_id[0:8]}` \n **Last updated:** {cur_time} \n {message}",
                failure = f"&#128308;  **{task_name}** \n ---------------------------- \n  **Status:** Failed `{task_id[0:8]}` \n **Last updated:** {cur_time} \n {message}")
    return response_template

def gh_filter(input_str):
    github_url_pattern = r'^https?://github\.com/([^/]+)/([^/]+)'
    match = re.match(github_url_pattern, input_str)
    if match:
        owner = match.group(1)
        repo_name = match.group(2)
        return f"{owner}/{repo_name}"
    else:
        return input_str

def gh_forkify_name(input_str):
    source_name = gh_filter(input_str)
    parts = source_name.split("/", 1)
    fork_name = GH_ORGANIZATION + "/" + parts[1]
    return fork_name

def gh_create_comment(github_client, issue_repo,issue_id,comment_body):
    repo = github_client.get_repo(gh_filter(issue_repo))
    issue = repo.get_issue(number=issue_id)
    commit_comment = issue.create_comment(comment_body)
    return commit_comment.id

def gh_update_comment(github_client, issue_repo,issue_id,comment_id,comment_body):
    repo = github_client.get_repo(gh_filter(issue_repo))
    issue = repo.get_issue(issue_id)
    comment = issue.get_comment(comment_id)
    comment.edit(comment_body)

def gh_template_respond(github_client,phase,task_name,repo,issue_id,task_id="",comment_id="", message=""):
    template = gh_response_template(task_name,task_id,message=message)
    if phase == "pending":
        # This one adds a new comment, returns comment_id
        return gh_create_comment(github_client,repo,issue_id,template['pending'])
    else:
        # This one updates comment, returns None
        return gh_update_comment(github_client,repo,issue_id,comment_id,template[phase])

def gh_get_project_name(github_client,target_repo):
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
    repo = github_client.get_repo(gh_filter(repo))
    try:
        file_content = repo.get_contents(file_path).decoded_content.decode()
    except Exception as e:
        print(f"Error retrieving file content: {str(e)}")
        return ""
    return file_content

def gh_update_file_content(github_client,repo,file_path,new_content,commit_message):
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
    file_content = gh_get_file_content(github_client,repo,"content/_config.yml")
    if file_content:
        yaml_data = yaml.safe_load(file_content)
    else:
        yaml_data = {}
    return yaml_data

def gh_update_jb_config(github_client,repo,content):
    updated_config = yaml.dump(content)
    response = gh_update_file_content(github_client,repo,"content/_config.yml",updated_config,":robot: [Automated] JB configuration update")
    return response

def gh_get_jb_toc(github_client,repo):
    file_content = gh_get_file_content(github_client,repo,"content/_toc.yml")
    if file_content:
        yaml_data = yaml.safe_load(file_content)
    else:
        yaml_data = {}
    return yaml_data

def gh_update_jb_toc(github_client,repo,content):
    updated_config = yaml.dump(content)
    response = gh_update_file_content(github_client,repo,"content/_toc.yml",updated_config,":robot: [Automated] JB TOC update")
    return response