import os
import re
import pytz
import datetime
import json


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