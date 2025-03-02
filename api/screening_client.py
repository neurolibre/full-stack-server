import os
import re
import pytz
import datetime
import json
import yaml
import git
from github import Github, InputFileContent
from common  import load_yaml, send_email
from dotenv import load_dotenv
from flask import jsonify, make_response

load_dotenv()

common_config = load_yaml("config/common.yaml")
GH_ORGANIZATION = common_config['GH_ORGANIZATION']
REVIEW_REPOSITORY = common_config['REVIEW_REPOSITORY']
JOURNAL_NAME = common_config['JOURNAL_NAME']

class ScreeningClient:
    def __init__(self, task_name, issue_id=None, email_address=None, target_repo_url = None, task_id=None, comment_id=None, commit_hash=None, notify_target=False, review_repository=REVIEW_REPOSITORY, **extra_payload):
        print(f"Debug - review_repository at init: {review_repository}")  # Debug line
        self.task_name = task_name
        self.task_id = task_id
        self.issue_id = issue_id
        self.email_address = email_address
        self.review_repository = review_repository
        self.target_repo_url = target_repo_url
        self.commit_hash = commit_hash
        self.comment_id = comment_id
        self.notify_target = notify_target
        self.__extra_payload = extra_payload
        self._init_responder()

        for key, value in extra_payload.items():
            setattr(self, key, value)

        # Private GitHub token
        self.__gh_bot_token = os.getenv('GH_BOT')
        self.github_client = Github(self.__gh_bot_token)
        if self.target_repo_url:
            self.repo_object = self.github_client.get_repo(self.gh_filter(self.target_repo_url))
        else:
            self.repo_object = None

        if (self.issue_id is not None) and (self.comment_id is None) and (self.notify_target):
            self.comment_id = self.respond.PENDING("Awaiting task assignment...")

        if (self.email_address is not None) and (self.target_repo_url is not None) and (self.notify_target):
            self.send_user_email(f"We have received a request for {self.target_repo_url}. \n Your task has been queued and we will inform you shortly when the process starts.")

    def _init_responder(self):
        class PhaseResponder:
            def __init__(self, phase, client):
                self.phase = phase
                self.client = client

            def __call__(self, message="", collapsable=True):
                template = self.client.gh_response_template(message=message, collapse=collapsable)
                if self.phase == "PENDING" or self.comment_id is None:
                    comment_id = self.client.gh_create_comment(template['PENDING'])
                    self.comment_id = comment_id
                    return comment_id
                else:
                    return self.client.gh_update_comment(template[self.phase])

        class ResponderContainer:
            def __init__(self, client):
                self.client = client
                self.PENDING = PhaseResponder("PENDING", client)
                self.RECEIVED = PhaseResponder("RECEIVED", client)
                self.STARTED = PhaseResponder("STARTED", client)
                self.SUCCESS = PhaseResponder("SUCCESS", client)
                self.FAILURE = PhaseResponder("FAILURE", client)
                self.EXISTS = PhaseResponder("EXISTS", client)

            def __getattr__(self, phase):
                if phase in self.__dict__:
                    return lambda message="", collapsable=True: self.__dict__[phase](message, collapsable)
                raise AttributeError(f"'{self.__class__.__name__}' object has no attribute '{phase}'")

        self.respond = ResponderContainer(self)

    def to_dict(self):
        # Convert the object to a dictionary to pass to Celery
        result = {
            'task_name': self.task_name,
            'issue_id': self.issue_id,
            'email_address': self.email_address,
            'target_repo_url': self.target_repo_url,
            'task_id': self.task_id,
            'comment_id': self.comment_id,
            'commit_hash': self.commit_hash,
            'review_repository': self.review_repository
        }
        result.update(self.__extra_payload)
        return result

    @classmethod
    def from_dict(cls, data):
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except json.JSONDecodeError:
                raise ValueError("Invalid JSON string provided")

        if not isinstance(data, dict):
            raise TypeError("Input must be a dictionary or a JSON string")
        standard_attrs = ['task_name', 'issue_id', 'email_address', 'target_repo_url', 'task_id', 'comment_id', 'commit_hash','review_repository']
        standard_dict = {key: data.get(key) for key in standard_attrs}
        extra_payload = {key: value for key, value in data.items() if key not in standard_attrs}
        return cls(**standard_dict, **extra_payload)

    def start_celery_task(self, celery_task_func):
        
        # This trick is needed to pass the ScreeningClient object to the Celery task.
        # This is because the ScreeningClient object cannot be serialized into JSON, which is required by Redis.
        task_result = celery_task_func.apply_async(args=[self.to_dict()])
        
        if task_result.task_id is not None:
            self.task_id = task_result.task_id
            message = f"Celery task assigned successfully. Task ID: {self.task_id}"
            self.respond.RECEIVED(message)
            return make_response(jsonify(message), 200)
        else:
            message = f"Celery task assignment failed. Task Name: {self.task_name}"
            self.respond.FAILURE(message, collapsable=False)
            return make_response(jsonify(message), 500)
        
    @staticmethod
    def is_not_blank(my_string):
        return bool(my_string and my_string.strip())

    def gh_response_template(self, message="", collapse=True):
        tz = pytz.timezone('US/Pacific')
        now = datetime.datetime.now(tz)
        cur_time = now.strftime('%Y-%m-%d %H:%M:%S %Z')

        if self.is_not_blank(message):
            if collapse:
                message = f"\n<details><summary> :information_source: See details </summary><pre><code>{message}</code></pre></details>"
            else:
                message = f"\n{message}"
        else:
            message = ""

        # If the comment ID is not set, set it to an empty string
        if self.comment_id is None:
            this_comment_id = ""
        else:
            this_comment_id = self.comment_id
        
        if self.task_id is None:
            this_task_id = "000000000000"
        else:
            this_task_id = self.task_id

        issue_comment_url = f"https://github.com/neurolibre/neurolibre-reviews/issues/{self.issue_id}#issuecomment-{this_comment_id}"
        return {
            "PENDING": f"&#9899; **{self.task_name}**\n----------------------------\n**Status:** Waiting for task assignment\n**Last updated:** {cur_time}\n{message}",
            "RECEIVED": f"&#9898; **{self.task_name}**\n----------------------------\n**Status:** Assigned to task `{this_task_id[0:8]}`\n**Last updated:** {cur_time}\n{message}\n:recycle: <a href=\"{issue_comment_url}\">Refresh</a>)",
            "STARTED": f"&#128992; **{self.task_name}**\n----------------------------\n**Status:** In progress `{this_task_id[0:8]}`\n**Last updated:** {cur_time}\n{message}\n:recycle: <a href=\"{issue_comment_url}\">Refresh</a>",
            "SUCCESS": f"&#128994; **{self.task_name}**\n----------------------------\n**Status:** Success `{this_task_id[0:8]}`\n**Last updated:** {cur_time}\n{message}",
            "FAILURE": f"&#128308; **{self.task_name}**\n----------------------------\n**Status:** Failed `{this_task_id[0:8]}`\n**Last updated:** {cur_time}\n{message}",
            "EXISTS": f"&#128995; **{self.task_name}**\n----------------------------\n**Status:** Already exists `{this_task_id[0:8]}`\n**Last updated:** {cur_time}\n{message}",
        }

    # @staticmethod
    # def gh_filter(input_str):
    #     github_url_pattern = r'^https?://(?:www\.)?github\.com/([^/]+)/([^/]+)'
    #     match = re.match(github_url_pattern, input_str)
    #     if match:
    #         owner = match.group(1)
    #         repo_name = match.group(2)
    #         return f"{owner}/{repo_name}"
    #     return input_str

    @staticmethod
    def gh_filter(input_str):
        if not input_str:
            raise ValueError("Repository URL/path cannot be None or empty")
        
        # If it's already in owner/repo format, return as is
        if re.match(r'^[^/]+/[^/]+$', input_str):
            return input_str
            
        # Otherwise, try to extract from URL
        github_url_pattern = r'^https?://(?:www\.)?github\.com/([^/]+)/([^/]+)'
        match = re.match(github_url_pattern, input_str)
        if match:
            owner = match.group(1)
            repo_name = match.group(2)
            return f"{owner}/{repo_name}"
        
        return input_str

    def gh_create_comment(self, comment_body, override_assign=False):
        repo = self.github_client.get_repo(self.gh_filter(self.review_repository))
        issue = repo.get_issue(number=self.issue_id)
        commit_comment = issue.create_comment(comment_body)
        if not override_assign:
            self.comment_id = commit_comment.id  # Update comment_id after creation
        return commit_comment.id

    def gh_update_comment(self, comment_body):
        repo = self.github_client.get_repo(self.gh_filter(self.review_repository))
        issue = repo.get_issue(self.issue_id)
        comment = issue.get_comment(self.comment_id)
        comment.edit(comment_body)

    def gh_get_project_name(self):
        repo = self.github_client.get_repo(self.gh_filter(self.review_repository))
        contents = repo.get_contents("binder/data_requirement.json")
        data = json.loads(contents.decoded_content)
        return data['projectName']

    def gh_fork_repository(self, source_repo):
        repo_to_fork = self.github_client.get_repo(self.gh_filter(source_repo))
        target_org = self.github_client.get_organization(self.GH_ORGANIZATION)
        forked_repo = target_org.create_fork(repo_to_fork)
        return forked_repo

    def gh_get_file_content(self, file_path):
        repo = self.github_client.get_repo(self.gh_filter(self.review_repository))
        try:
            file_content = repo.get_contents(file_path).decoded_content.decode()
        except Exception as e:
            print(f"Error retrieving file content: {str(e)}")
            return ""
        return file_content

    def gh_update_file_content(self, file_path, new_content, commit_message):
        repo = self.github_client.get_repo(self.gh_filter(self.review_repository))
        try:
            file = repo.get_contents(file_path)
            repo.update_file(file.path, commit_message, new_content, file.sha)
            return {"status": True, "message": "Success"}
        except Exception as e:
            return {"status": False, "message": str(e)}

    def gh_get_jb_config(self):
        file_content = self.gh_get_file_content("content/_config.yml")
        if file_content:
            yaml_data = yaml.safe_load(file_content)
        else:
            yaml_data = {}
        return yaml_data

    def gh_update_jb_config(self, content):
        updated_config = yaml.dump(content)
        return self.gh_update_file_content("content/_config.yml", updated_config, ":robot: [Automated] JB configuration update")

    def gh_get_jb_toc(self):
        file_content = self.gh_get_file_content("content/_toc.yml")
        if file_content:
            yaml_data = yaml.safe_load(file_content)
        else:
            yaml_data = {}
        return yaml_data

    def gh_update_jb_toc(self, content):
        updated_config = yaml.dump(content)
        return self.gh_update_file_content("content/_toc.yml", updated_config, ":robot: [Automated] JB TOC update")

    def gh_get_paper_markdown(self):
        return self.gh_get_file_content("paper.md")

    def gh_read_from_issue_body(self, tag):
        repo = self.github_client.get_repo(self.gh_filter(self.review_repository))
        issue = repo.get_issue(self.issue_id)
        issue_body = issue.body
        start_marker = f"<!--{tag}-->"
        end_marker = f"<!--end-{tag}-->"
        start_index = issue_body.find(start_marker)
        end_index = issue_body.find(end_marker)
        if start_index != -1 and end_index != -1:
            extracted_text = issue_body[start_index + len(start_marker):end_index].strip()
            if extracted_text == "Pending":
                extracted_text = None
        else:
            extracted_text = None
        return extracted_text

    def get_default_branch(self):
        repo = self.github_client.get_repo(self.gh_filter(self.review_repository))
        return repo.default_branch

    @staticmethod
    def gh_clone_repository(repo_url, target_path, depth=1):
        if not os.path.exists(target_path):
            os.makedirs(target_path)
        git.Repo.clone_from(repo_url, target_path, depth=depth)

    def STATE_WITH_ATTACHMENT(self, message, file_path, failure=False):
        """Post failure message with file attachment to GitHub issue"""
        
        # Create a gist with the file content
        with open(file_path, 'r') as f:
            content = f.read()
            
        gist = self.github_client.get_user().create_gist(
            public=True,
            files={os.path.basename(file_path): InputFileContent(content)},
            description=f"Attachment for {self.task_name} logs"
        )
        
        # Add gist link to message
        message += f"\n\n[Download logs]({gist.html_url})"
        
        # Post comment with gist link
        if failure:
            self.respond.FAILURE(message, False)
        else:
            self.respond.SUCCESS(message, False)
    
    def send_user_email(self, body):
        send_email(self.email_address, self.task_name, body)