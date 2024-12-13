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
    user_name = fields.String(required=False,description="Return NeuroLibre reproducible preprints that match a user (owner) name (suggested to be used in addition to the repo_name)")
    commit_hash = fields.String(required=False,description="Return NeuroLibre reproducible preprints built at the requested commit hash.")
    repo_name = fields.String(required=False,description="Return NeuroLibre reproducible preprints for a repository name (suggested to be used in addition to the user_name).")

# Preview server

class DownloadSchema(Schema):
    """
    Defines schema to be used for repo2data download. 
    """
    repository_url = fields.Str(required=True,dump_default="",description="Full URL of a NeuroLibre compatible repository to be used for building the book.")
    id = fields.Integer(required=False,description="Issue number of the technical screening of this preprint. If this used, the response will be returned to the respective github issue.")
    email = fields.Str(required=False,dump_default="",description="Email address, to which the result will be returned.")
    is_overwrite = fields.Boolean(required=False,dump_default="",description="Whether or not the downloaded data will overwrite, if already exists.")
    external_repo = fields.Str(required=False,dump_default="",description="A non-review repo.")

class BuildSchema(Schema):
    """
    Defines payload types and requirements for book build request.
    """
    id = fields.Integer(required=True,description="Issue number of the technical screening of this preprint.")
    repo_url = fields.Str(required=True,description="Full URL of a NeuroLibre compatible repository to be used for building the book.")
    commit_hash = fields.String(required=True,dump_default="HEAD",description="Commit SHA to be checked out for building the book. Defaults to HEAD.")

class MystBuildSchema(Schema):
    """
    Defines payload types and requirements for book build request.
    """
    id = fields.Integer(required=True,description="Issue number of the technical screening of this preprint.")
    repository_url = fields.Str(required=True,description="Full URL of a NeuroLibre compatible repository to be used for building the book.")
    commit_hash = fields.String(required=False,dump_default="HEAD",description="Commit SHA to be checked out for building the book. Defaults to HEAD.")
    binder_hash = fields.String(required=False,dump_default="HEAD",description="Commit SHA at which a binder image was built successfully.")
    is_prod = fields.Boolean(required=False,dump_default=False,description="Whether or not the build is intended for production.")

class BuildTestSchema(Schema):
    """
    Defines payload types and requirements for book build request (from robo.neurolibre.org).
    """
    repo_url = fields.Str(required=True,description="Full URL of a NeuroLibre compatible repository to be used for building the book.")
    commit_hash = fields.String(required=True,dump_default="HEAD",description="Commit SHA to be checked out for building the book. Defaults to HEAD.")
    email = fields.Str(required=True,description="Email address to send the response.")

class IDSchema(Schema):
    id = fields.Integer(required=True,description="Issue number of the technical screening of this preprint.")

class UploadSchema(Schema):
    issue_id = fields.Int(required=True,description="Issue number of the technical screening of this preprint.") 
    repository_address = fields.String(required=True,description="Full URL of the repository submitted by the author.")
    item = fields.String(required=True,description="One of the following: | book | repository | data | docker |")
    item_arg = fields.String(required=True,description="Additional information to locate the item on the server. Needed for items data and docker.")
    fork_url = fields.String(required=True,description="Full URL of the forked (roboneurolibre) repository.")
    commit_fork = fields.String(required=True,description="Commit sha at which the forked repository (and other resources) will be deposited")

class ListSchema(Schema):
    issue_id = fields.Int(required=True,description="Issue number of the technical screening of this preprint.")

class IdUrlSchema(Schema):
    id = fields.Integer(required=True,description="Issue number of the technical screening of this preprint.")
    repository_url = fields.String(required=True,description="Full URL of the target repository")

class BooksyncSchema(Schema):
    id = fields.Integer(required=True,description="Issue number of the technical screening of this preprint.")
    repository_url = fields.String(required=True,description="Full URL of the target repository")
    commit_hash = fields.String(required=False,dump_default="HEAD", description="Commit hash.")