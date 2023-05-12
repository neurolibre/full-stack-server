from marshmallow import Schema, fields

# Common

class StatusSchema(Schema):
    id = fields.Integer(required=False,description="Review issue ID if request is forwarded through robo.neurolibre.org")

class UnlockSchema(Schema):
    """
    Defines payload types for removing a lock for target repository build.
    """
    repo_url = fields.Str(required=True,description="Full URL of the target repository.")

class TaskSchema(Schema):
    task_id = fields.String(required=True,description="Celery task ID.")

class BookSchema(Schema):
    user_name = fields.String(required=False,description="Return NeuroLibre reproducible preperints that match a user (owner) name (suggested to be used in addition to the repo_name)")
    commit_hash = fields.String(required=False,description="Return NeuroLibre reproducible preprints built at the requested commit hash.")
    repo_name = fields.String(required=False,description="Return NeuroLibre reproducible preprints for a repository name (suggested to be used in addition to the user_name).")

# Preview server

class BuildSchema(Schema):
    """
    Defines payload types and requirements for book build request.
    """
    repo_url = fields.Str(required=True,description="Full URL of a NeuroLibre compatible repository to be used for building the book.")
    commit_hash = fields.String(required=True,dump_default="HEAD",description="Commit SHA to be checked out for building the book. Defaults to HEAD.")

# Preprint server

class BinderSchema(Schema):
    """
    Defines payload types and requirements for binderhub build request.
    """
    repo_url = fields.Str(required=True,description="Full URL of a roboneurolibre repository.")
    commit_hash = fields.String(required=True,dump_default="HEAD",description="Commit SHA to be checked out for the build. Defaults to HEAD.")

class BucketsSchema(Schema):
    """
    Defines payload types and requirements for creating zenodo records.
    """
    fork_url = fields.Str(required=True,description="Full URL of the forked (roboneurolibre) repository.")
    user_url = fields.Str(required=True,description="Full URL of the repository submitted by the author.")
    commit_fork = fields.String(required=True,description="Commit sha at which the forked repository (and other resources) will be deposited")
    commit_user = fields.String(required=True,description="Commit sha at which the user repository was forked into roboneurolibre.")
    title = fields.String(required=True,description="Title of the submitted preprint. Each Zenodo record will attain this title.")
    issue_id = fields.Int(required=True,description="Issue number of the technical screening of this preprint.")
    creators = fields.List(fields.Str(),required=True,description="List of the authors.")
    deposit_data = fields.Boolean(required=True,description="Determines whether Zenodo will deposit the data provided by the user.")

class UploadSchema(Schema):
    issue_id = fields.Int(required=True,description="Issue number of the technical screening of this preprint.") 
    repository_address = fields.String(required=True,description="Full URL of the repository submitted by the author.")
    item = fields.String(required=True,description="One of the following: | book | repository | data | docker |")
    item_arg = fields.String(required=True,description="Additional information to locate the item on the server. Needed for items data and docker.")
    fork_url = fields.String(required=True,description="Full URL of the forked (roboneurolibre) repository.")
    commit_fork = fields.String(required=True,description="Commit sha at which the forked repository (and other resources) will be deposited")

class ListSchema(Schema):
    issue_id = fields.Int(required=True,description="Issue number of the technical screening of this preprint.")

class DeleteSchema(Schema):
    issue_id = fields.Int(required=True,description="Issue number of the technical screening of this preprint.")
    items = fields.List(fields.Str(),required=True,description="List of the items to be deleted from Zenodo.")

class PublishSchema(Schema):
    issue_id = fields.Int(required=True,description="Issue number of the technical screening of this preprint.")

class DatasyncSchema(Schema):
    id = fields.Integer(required=True,description="Issue number of the technical screening of this preprint.")
    repository_url = fields.String(required=True,description="Full URL of the target repository")

class BooksyncSchema(Schema):
    id = fields.Integer(required=True,description="Issue number of the technical screening of this preprint.")
    repository_url = fields.String(required=True,description="Full URL of the target repository")
    commit_hash = fields.String(required=False,dump_default="HEAD", description="Commit hash.")