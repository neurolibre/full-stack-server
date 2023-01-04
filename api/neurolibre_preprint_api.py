from common import *
from preprint import *
import flask
import os
import json
import glob
import time
import subprocess
import requests
import shutil
import git
from flask_htpasswd import HtPasswdAuth
from dotenv import load_dotenv
from werkzeug.middleware.proxy_fix import ProxyFix
import logging
import neurolibre_common_api
from flask_apispec import FlaskApiSpec, marshal_with, doc, use_kwargs


from apispec import APISpec
from apispec.ext.marshmallow import MarshmallowPlugin
from marshmallow import Schema, fields
 
# THIS IS NEEDED UNLESS FLASK IS CONFIGURED TO AUTO-LOAD!
load_dotenv()

app = flask.Flask(__name__)

app.register_blueprint(neurolibre_common_api.common_api)

app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
app.config["DEBUG"] = True

gunicorn_error_logger = logging.getLogger('gunicorn.error')
app.logger.handlers.extend(gunicorn_error_logger.handlers)
app.logger.setLevel(logging.DEBUG)
app.logger.debug('NeuroLibre preprint production API.')

AUTH_KEY=os.getenv('AUTH_KEY')
app.config['FLASK_HTPASSWD_PATH'] = AUTH_KEY
htpasswd = HtPasswdAuth(app)

binderName = "binder-mcgill"
domainName = "conp.cloud"
logo ="<img style=\"width:200px;\" src=\"https://github.com/neurolibre/brand/blob/main/png/logo_preprint.png?raw=true\"></img>"
serverName = 'neurolibre-data-prod' # e.g. preprint.conp.cloud
serverDescription = 'Production server'
serverContact = dict(name="NeuroLibre",url="https://neurolibre.org",email="conpdev@gmail.com")
serverTOS = "http://docs.neurolibre.org"
serverAbout = f"<h3>Endpoints to handle publishing tasks <u>following the completion of</u> the technical screening process.</h3>{logo}"

spec = APISpec(
        title="Reproducible preprint API",
        version='v1',
        plugins=[MarshmallowPlugin()],
        openapi_version="3.0.2",
        info=dict(description=serverAbout,contact=serverContact,termsOfService=serverTOS),
        servers = [{'url': 'https://{serverName}.{domainName}/','description':'Production server.', 'variables': {'serverName':{'default':serverName},'domainName':{'default':domainName}}}]
        )

app.config.update({
    'APISPEC_SPEC': spec,
    'APISPEC_SWAGGER_URL': '/swagger/',
    'APISPEC_SWAGGER_UI_URL': '/documentation'
})

api_key_scheme = {"type": "http", "scheme": "basic"}
spec.components.security_scheme("basicAuth", api_key_scheme)

# Create swagger UI documentation for the endpoints.
docs = FlaskApiSpec(app=app,document_options=False)

# Register common endpoints to the documentation
docs.register(neurolibre_common_api.api_books_get,blueprint="common_api")

# TODO: Replace yield stream with lists for most of the routes. 
# You can get rid of run() and return a list instead. This way we can refactor 
# and move server-side ops functions elsewhere to make endpoint descriptions clearer.

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

@app.route('/api/zenodo/buckets', methods=['POST'])
@htpasswd.required
@marshal_with(None,code=422,description="Cannot validate the payload, missing or invalid entries.")
@doc(description='Create zenodo buckets (i.e., records) for a submission.', tags=['Zenodo'])
@use_kwargs(BucketsSchema())
def api_zenodo_post(user,fork_url,user_url,commit_fork,commit_user, title,issue_id,creators,deposit_data):
    """
    Fetches kwargs from validated BucketsSchema and makes bucket request to zenodo.
    """
    def run():
        ZENODO_TOKEN = os.getenv('ZENODO_API')
        headers = {"Content-Type": "application/json", "Authorization": "Bearer {}".format(ZENODO_TOKEN)}
        fname = f"zenodo_deposit_NeuroLibre_{'%05d'%issue_id}.json"
        local_file = os.path.join(get_deposit_dir(issue_id), fname)
        
        collect = {}
        if os.path.exists(local_file):
            # File already exists, do nothing.
            collect["message"] = f"Zenodo records already exist for this submission on NeuroLibre servers: {fname}"
        else:
            # File does not exist, move on to determine for which resources
            # zenodo buckets will be created.
            if deposit_data:
                # User does not have DOI'd data, we'll create.
                resources = ["book","repository","data","docker"]
            else:
                # Do not create a record for data, user already did.
                resources = ["book","repository","docker"]
            for archive_type in resources:
                r = zenodo_create_bucket(title, archive_type, creators, user_url, fork_url, commit_user, commit_fork, issue_id)
                collect[archive_type] = r
                time.sleep(0.5)
            if {k: v for k, v in collect.items() if 'reason' in v}:
                # This means at least one of the deposits has failed.
                print('Caught an issue with the deposit. A record (JSON) will not be created.')
                # Delete deposition if succeeded for a certain resource
                remove_dict = {k: v for k, v in collect.items() if not 'reason' in v }
                for key in remove_dict:
                    print("Deleting " + remove_dict[key]["links"]["self"])
                    tmp = requests.delete(remove_dict[key]["links"]["self"], headers=headers)
                    time.sleep(0.5)
                    # Returns 204 if successful, cast str to display
                    collect[key + "_deleted"] = str(tmp)
            else:
                # This means that all requested deposits are successful
                print(f'Writing {local_file}...')
                with open(local_file, 'w') as outfile:
                    json.dump(collect, outfile)

        # The response will be returned to the caller regardless of the state.
        yield "\n" + json.dumps(collect)
        yield ""
    return flask.Response(run(), mimetype='text/plain')

# Register endpoint to the documentation
docs.register(api_zenodo_post)


class UploadSchema(Schema):
    issue_id = fields.Int(required=True,description="Issue number of the technical screening of this preprint.") 
    repository_address = fields.String(required=True,description="Full URL of the repository submitted by the author.")
    item = fields.String(required=True,description="One of the following: | book | repository | data | docker |")
    item_arg = fields.String(required=True,description="Additional information to locate the item on the server. Needed for items data and docker.")
    fork_url = fields.String(required=True,description="Full URL of the forked (roboneurolibre) repository.")
    commit_fork = fields.String(required=True,description="Commit sha at which the forked repository (and other resources) will be deposited")

@app.route('/api/zenodo/upload', methods=['POST'])
@htpasswd.required
@marshal_with(None,code=422,description="Cannot validate the payload, missing or invalid entries.")
@doc(description='Upload an item to the respective zenodo bucket (book, repository, data or docker image).', tags=['Zenodo'])
@use_kwargs(UploadSchema())
def api_upload_post(user,issue_id,repository_address,item,item_arg,fork_url,commit_fork):
    """
    Uploads one item at a time (book, repository, data or docker image) to zenodo 
    for the buckets that have been created.
    """
    repofork = fork_url.split("/")[-1]
    fork_repo = fork_url.split("/")[-2]
    fork_provider = fork_url.split("/")[-3]
    if not ((fork_provider == "github.com") | (fork_provider == "gitlab.com")):
        flask.abort(400)
    def run():
        ZENODO_TOKEN = os.getenv('ZENODO_API')
        params = {'access_token': ZENODO_TOKEN}
        # Read json record of the deposit
        fname = f"zenodo_deposit_NeuroLibre_{'%05d'%issue_id}.json"
        local_file = os.path.join(get_deposit_dir(issue_id), fname)
        with open(local_file, 'r') as f:
            zenodo_record = json.load(f)
        # Fetch bucket url of the requested type of item
        bucket_url = zenodo_record[item]['links']['bucket']
        if item == "book":
           # We will archive the book created through the forked repository.
           local_path = os.path.join("/DATA", "book-artifacts", fork_repo, fork_provider, repofork, commit_fork, "_build", "html")
           # Descriptive file name
           zenodo_file = os.path.join(get_archive_dir(issue_id),f"JupyterBook_10.55458_NeuroLibre_{'%05d'%issue_id}_{commit_fork[0:6]}")
           # Zip it!
           shutil.make_archive(zenodo_file, 'zip', local_path)
           zpath = zenodo_file + ".zip"
        
           with open(zpath, "rb") as fp:
            r = requests.put(f"{bucket_url}/JupyterBook_10.55458_NeuroLibre_{'%05d'%issue_id}_{commit_fork[0:6]}.zip",
                                    params=params,
                                    data=fp)
           if not r:
            error = {"reason":f"404: Cannot upload {zpath} to {bucket_url}", "commit_hash":commit_fork, "repo_url":fork_repo,"issue_id":issue_id}
            yield "\n" + json.dumps(error)
            yield ""
           else:
            tmp = f"zenodo_uploaded_{item}_NeuroLibre_{'%05d'%issue_id}_{commit_fork[0:6]}.json"
            log_file = os.path.join(get_deposit_dir(issue_id), tmp)
            with open(log_file, 'w') as outfile:
                    json.dump(r.json(), outfile)
            
            yield "\n" + json.dumps(r.json())
            yield ""

        elif item == "docker":

            # If already exists, do not pull again, but let them know.
            expect = os.path.join(get_archive_dir(issue_id),f"DockerImage_10.55458_NeuroLibre_{'%05d'%issue_id}_{commit_fork[0:6]}.tar.gz")
            check_docker = os.path.exists(expect)

            if check_docker:
                yield f"\n already exists {expect}"
                yield f"\n uploading to zenodo"
                with open(expect, "rb") as fp:
                        r = requests.put(f"{bucket_url}/DockerImage_10.55458_NeuroLibre_{'%05d'%issue_id}_{commit_fork[0:6]}.zip",
                                        params=params,
                                        data=fp)
                # TO_DO: Write a function to handle this, too many repetitions rn.
                if not r:
                    error = {"reason":f"404: Cannot upload {in_r[1]} to {bucket_url}", "commit_hash":commit_fork, "repo_url":fork_repo,"issue_id":issue_id}
                    yield "\n" + json.dumps(error)
                    yield ""
                else:
                    tmp = f"zenodo_uploaded_{item}_NeuroLibre_{'%05d'%issue_id}_{commit_fork[0:6]}.json"
                    log_file = os.path.join(get_deposit_dir(issue_id), tmp)
                    with open(log_file, 'w') as outfile:
                            json.dump(r.json(), outfile)

                    yield "\n" + json.dumps(r.json())
                    yield ""
            else:
                docker_login()
                # Docker image address should be here
                docker_pull(item_arg)
                in_r = docker_export(item_arg,issue_id,commit_fork)
                # in_r[0] os.system status, in_r[1] saved docker image absolute path

                docker_logout()
                if in_r[0] == 0:
                    # Means that saved successfully, upload to zenodo.
                    with open(in_r[1], "rb") as fp:
                        r = requests.put(f"{bucket_url}/DockerImage_10.55458_NeuroLibre_{'%05d'%issue_id}_{commit_fork[0:6]}.zip",
                                        params=params,
                                        data=fp)
                    # TO_DO: Write a function to handle this, too many repetitions rn.
                    if not r:
                        error = {"reason":f"404: Cannot upload {in_r[1]} to {bucket_url}", "commit_hash":commit_fork, "repo_url":fork_repo,"issue_id":issue_id}
                        yield "\n" + json.dumps(error)
                        yield ""
                    else:
                        tmp = f"zenodo_uploaded_{item}_NeuroLibre_{'%05d'%issue_id}_{commit_fork[0:6]}.json"
                        log_file = os.path.join(get_deposit_dir(issue_id), tmp)
                        with open(log_file, 'w') as outfile:
                                json.dump(r.json(), outfile)

                        yield "\n" + json.dumps(r.json())
                        yield ""
                else:
                # Cannot save docker image succesfully
                    error = {"reason":f"404: Cannot save requested docker image as tar.gz: {item_arg}", "commit_hash":commit_fork, "repo_url":fork_repo,"issue_id":issue_id}
                    yield "\n" + json.dumps(error)
                    yield ""

        elif item == "repository":
            
            download_url_main = f"{fork_url}/archive/refs/heads/main.zip"
            download_url_master = f"{fork_url}/archive/refs/heads/master.zip"

            zenodo_file = os.path.join(get_archive_dir(issue_id),f"GitHubRepo_10.55458_NeuroLibre_{'%05d'%issue_id}_{commit_fork[0:6]}.zip")
            
            # REFACTOR HERE AND MANAGE CONDITIONS CLEANER.
            # Try main first
            resp = os.system(f"wget -O {zenodo_file} {download_url_main}")
            if resp != 0:
                # Try master 
                resp2 = os.system(f"wget -O {zenodo_file} {download_url_master}")
                if resp2 != 0:
                    error = {"reason":f"404: Cannot download repository at {download_url_main} or from master branch.", "commit_hash":commit_fork, "repo_url":fork_repo,"issue_id":issue_id}
                    yield "\n" + json.dumps(error)
                    yield ""
                    # TRY FLASK.ABORT(code,custom) here for refactoring.
                else:
                    # Upload to Zenodo
                    with open(zenodo_file, "rb") as fp:
                        r = requests.put(f"{bucket_url}/GitHubRepo_10.55458_NeuroLibre_{'%05d'%issue_id}_{commit_fork[0:6]}.zip",
                                        params=params,
                                        data=fp)
                        if not r:
                            error = {"reason":f"404: Cannot upload {zenodo_file} to {bucket_url}", "commit_hash":commit_fork, "repo_url":fork_repo,"issue_id":issue_id}
                            yield "\n" + json.dumps(error)
                            yield ""
                        else:
                            tmp = f"zenodo_uploaded_{item}_NeuroLibre_{'%05d'%issue_id}_{commit_fork[0:6]}.json"
                            log_file = os.path.join(get_deposit_dir(issue_id), tmp)
                            with open(log_file, 'w') as outfile:
                                    json.dump(r.json(), outfile)
                        # Return answer to flask
                        yield "\n" + json.dumps(r.json())
                        yield ""
            else: 
                # main worked
                # Upload to Zenodo
                with open(zenodo_file, "rb") as fp:
                    r = requests.put(f"{bucket_url}/GitHubRepo_10.55458_NeuroLibre_{'%05d'%issue_id}_{commit_fork[0:6]}.zip",
                                    params=params,
                                    data=fp)
                    if not r:
                            error = {"reason":f"404: Cannot upload {zenodo_file} to {bucket_url}", "commit_hash":commit_fork, "repo_url":fork_repo,"issue_id":issue_id}
                            yield "\n" + json.dumps(error)
                            yield ""
                    else:
                        tmp = f"zenodo_uploaded_{item}_NeuroLibre_{'%05d'%issue_id}_{commit_fork[0:6]}.json"
                        log_file = os.path.join(get_deposit_dir(issue_id), tmp)
                        with open(log_file, 'w') as outfile:
                                json.dump(r.json(), outfile)
                        # Return answer to flask
                        yield "\n" + json.dumps(r.json())
                        yield ""

        elif item == "data":

           expect = os.path.join(get_archive_dir(issue_id),f"Dataset_10.55458_NeuroLibre_{'%05d'%issue_id}_{commit_fork[0:6]}.zip")
           check_data = os.path.exists(expect)

           if check_data:
            yield f"\n Compressed data already exists Dataset_10.55458_NeuroLibre_{'%05d'%issue_id}_{commit_fork[0:6]}.zip"
            zpath = expect
           else:
            # We will archive the data synced from the test server. (item_arg is the project_name, indicating that the 
            # data is stored at the /DATA/project_name folder)
            local_path = os.path.join("/DATA", item_arg)
            # Descriptive file name
            zenodo_file = os.path.join(get_archive_dir(issue_id),f"Dataset_10.55458_NeuroLibre_{'%05d'%issue_id}_{commit_fork[0:6]}")
            # Zip it!
            shutil.make_archive(zenodo_file, 'zip', local_path)
            zpath = zenodo_file + ".zip"

           # UPLOAD data to zenodo
           yield f"\n Attempting zenodo upload."
           with open(zpath, "rb") as fp:
            r = requests.put(f"{bucket_url}/Dataset_10.55458_NeuroLibre_{'%05d'%issue_id}_{commit_fork[0:6]}.zip",
                                    params=params,
                                    data=fp)

            if not r:
                error = {"reason":f"404: Cannot upload {zenodo_file} to {bucket_url}", "commit_hash":commit_fork, "repo_url":fork_repo,"issue_id":issue_id}
                yield "\n" + json.dumps(error)
                yield ""
            else:
                tmp = f"zenodo_uploaded_{item}_NeuroLibre_{'%05d'%issue_id}_{commit_fork[0:6]}.json"
                log_file = os.path.join(get_deposit_dir(issue_id), tmp)
                with open(log_file, 'w') as outfile:
                        json.dump(r.json(), outfile)
                # Return answer to flask
                yield "\n" + json.dumps(r.json())
                yield ""

    return flask.Response(run(), mimetype='text/plain')

# Register endpoint to the documentation
docs.register(api_upload_post)

class ListSchema(Schema):
    issue_id = fields.Int(required=True,description="Issue number of the technical screening of this preprint.") 

@app.route('/api/zenodo/list', methods=['POST'])
@htpasswd.required
@marshal_with(None,code=422,description="Cannot validate the payload, missing or invalid entries.")
@doc(description='Get the list of Zenodo records that are available for a given submission.', tags=['Zenodo'])
@use_kwargs(ListSchema())
def api_zenodo_list_post(user,issue_id):
    """
    List zenodo records for a given technical screening ID.
    """
    def run():
        path = f"/DATA/zenodo_records/{'%05d'%issue_id}"
        if not os.path.exists(path):
            yield "<br> :neutral_face: I could not find any Zenodo-related records on NeuroLibre servers. Maybe start with `roboneuro zenodo deposit`?"
        else:
            files = os.listdir(path)
            yield "<br> These are the Zenodo records I have on NeuroLibre servers:"
            yield "<ul>"
            for file in files:
                yield f"<li>{file}</li>"
            yield "</ul>"
    return flask.Response(run(), mimetype='text/plain')

# Register endpoint to the documentation
docs.register(api_zenodo_list_post)

class DeleteSchema(Schema):
    issue_id = fields.Int(required=True,description="Issue number of the technical screening of this preprint.")
    items = fields.List(fields.Str(),required=True,description="List of the items to be deleted from Zenodo.") 

@app.route('/api/zenodo/flush', methods=['POST'])
@htpasswd.required
@marshal_with(None,code=422,description="Cannot validate the payload, missing or invalid entries.")
@doc(description='Flush records and remove respective uploads from Zenodo, if available for a submission ID.', tags=['Zenodo'])
@use_kwargs(DeleteSchema())
def api_zenodo_flush_post(user,issue_id,items):
    """
    Delete buckets and uploaded files from zenodo if exist for a requested item type.
    """
    def run():
    # Set env
        ZENODO_TOKEN = os.getenv('ZENODO_API')
        headers = {"Content-Type": "application/json","Authorization": "Bearer {}".format(ZENODO_TOKEN)}
        # Read json record of the deposit
        fname = f"zenodo_deposit_NeuroLibre_{'%05d'%issue_id}.json"
        local_file = os.path.join(get_deposit_dir(issue_id), fname)
        dat2recmap = {"data":"Dataset","repository":"GitHubRepo","docker":"DockerImage","book":"JupyterBook"}
        
        with open(local_file, 'r') as f:
            zenodo_record = json.load(f)

        for item in items: 
            self_url = zenodo_record[item]['links']['self']
            # Delete the deposit
            r3 = requests.delete(self_url,headers=headers)
            if r3.status_code == 204:
                yield f"\n Deleted {item} deposit successfully at {self_url}."
                yield ""
                # We need to delete these from the Zenodo records file
                if item in zenodo_record: del zenodo_record[item]
                # Flush ALL the upload records (json) associated with the item
                tmp_record = glob.glob(os.path.join(get_deposit_dir(issue_id),f"zenodo_uploaded_{item}_NeuroLibre_{'%05d'%issue_id}_*.json"))
                if tmp_record:
                    for f in tmp_record:
                        os.remove(f)
                        yield f"\n Deleted {f} record from the server."
                # Flush ALL the uploaded files associated with the item
                tmp_file = glob.glob(os.path.join(get_archive_dir(issue_id),f"{dat2recmap[item]}_10.55458_NeuroLibre_{'%05d'%issue_id}_*.zip"))
                if tmp_file:
                    for f in tmp_file:
                        os.remove(f)
                        yield f"\n Deleted {f} record from the server."
            elif r3.status_code == 403: 
                yield f"\n The {item} archive has already been published, cannot be deleted."
                yield ""
            elif r3.status_code == 410:
                yield f"\n The {item} deposit does not exist."
                yield ""
        # Write zenodo record json file or rm existing one if empty at this point
        # Delete the old one
        os.remove(local_file)
        yield f"\n Deleted old {local_file} record from the server."
        # Write the new one
        if zenodo_record:
            with open(local_file, 'w') as outfile:
                json.dump(zenodo_record, outfile)
            yield f"\n Created new {local_file}."
        else:
            yield f"\n All the deposit records have been deleted."

    return flask.Response(run(), mimetype='text/plain')

# Register endpoint to the documentation
docs.register(api_zenodo_flush_post)

class PublishSchema(Schema):
    issue_id = fields.Int(required=True,description="Issue number of the technical screening of this preprint.") 

@app.route('/api/zenodo/publish', methods=['POST'])
@htpasswd.required
@marshal_with(None,code=422,description="Cannot validate the payload, missing or invalid entries.")
@doc(description='Publish uploaded zenodo records for archival for a given submission ID.', tags=['Zenodo'])
@use_kwargs(PublishSchema())
def api_zenodo_publish(user,issue_id):
    def run():
        ZENODO_TOKEN = os.getenv('ZENODO_API')
        params = {'access_token': ZENODO_TOKEN}
        # Read json record of the deposit
        fname = f"zenodo_deposit_NeuroLibre_{'%05d'%issue_id}.json"
        local_file = os.path.join(get_deposit_dir(issue_id), fname)
        dat2recmap = {"data":"Dataset","repository":"GitHub repository","docker":"Docker image","book":"Jupyter Book"}
        with open(local_file, 'r') as f:
            zenodo_record = json.load(f)
        if not os.path.exists(local_file):
            yield "<br> :neutral_face: I could not find any Zenodo-related records on NeuroLibre servers. Maybe start with <code>roboneuro zenodo deposit</code>?"
        else:
            # If there's a record, make sure that uploads are complete for all kind of items found in the deposit records.
            bool_array = []
            for item in zenodo_record.keys():
                tmp = glob.glob(os.path.join(get_deposit_dir(issue_id),f"zenodo_uploaded_{item}_NeuroLibre_{'%05d'%issue_id}_*.json"))
                if tmp:
                    bool_array.append(True)
                else:
                    bool_array.append(False)
            
            if all(bool_array):
                # We need self links from each record to publish.
                for item in zenodo_record.keys():
                    publish_link = zenodo_record[item]['links']['publish']
                    yield f"\n :ice_cube: {dat2recmap[item]} publish status:"
                    r = requests.post(publish_link,params=params)
                    response = r.json()
                    if r.status_code==202: 
                        yield f"\n :confetti_ball: <a href=\"{response['doi_url']}\"><img src=\"{response['links']['badge']}\"></a>"
                        tmp = f"zenodo_published_{item}_NeuroLibre_{'%05d'%issue_id}.json"
                        log_file = os.path.join(get_deposit_dir(issue_id), tmp)
                        with open(log_file, 'w') as outfile:
                            json.dump(r.json(), outfile)
                    else:
                        yield f"\n <details><summary> :wilted_flower: Could not publish {dat2recmap[item]} </summary><pre><code>{r.json()}</code></pre></details>"
            else:
                yield "\n :neutral_face: Not all archives are uploaded for the resources listed in the deposit record. Please ask <code>roboneuro zenodo status</code> and upload the missing (xxx) archives by <code>roboneuro zenodo archive-xxx</code>."

    return flask.Response(run(), mimetype='text/plain')

# Register endpoint to the documentation
docs.register(api_zenodo_publish)

class DatasyncSchema(Schema):
    project_name = item = fields.String(required=True,description="Unique project name described for the submission.")

@app.route('/api/data/sync', methods=['POST'])
@htpasswd.required
@marshal_with(None,code=422,description="Cannot validate the payload, missing or invalid entries.")
@doc(description='Transfer data from the preview to the production server based on the project name.', tags=['Data'])
@use_kwargs(DatasyncSchema())
def api_data_sync_post(user,project_name):
    # transfer with rsync
    remote_path = os.path.join("neurolibre-test-api:", "DATA", project_name)
    try:
        f = open("/DATA/data_synclog.txt", "a")
        f.write(remote_path)
        f.close()
        subprocess.check_call(["rsync", "-avR", remote_path, "/"])
    except subprocess.CalledProcessError:
        flask.abort(404)
    # final check
    if len(os.listdir(os.path.join("/DATA", project_name))) == 0:
        return {"reason": "404: Data sync was not successfull.", "project_name": project_name}
    else:
        return {"reason": "200: Data sync succeeded."}

# Register endpoint to the documentation
docs.register(api_data_sync_post)

class BooksyncSchema(Schema):
    repository_url = fields.String(required=True,description="Full URL of the repository submitted by the author.")
    commit_hash = fields.String(required=False,description="Commit hash.")

@app.route('/api/books/sync', methods=['POST'])
@htpasswd.required
@marshal_with(None,code=422,description="Cannot validate the payload, missing or invalid entries.")
@doc(description='Transfer a built book from the preview to the production server based on the project name.', tags=['Book'])
@use_kwargs(BooksyncSchema())
def api_books_sync_post(user,repo_url,commit_hash=None):
    repo = repo_url.split("/")[-1]
    user_repo = repo_url.split("/")[-2]
    provider = repo_url.split("/")[-3]
    if not ((provider == "github.com") | (provider == "gitlab.com")):
        flask.abort(400)
    if commit_hash:
        commit = commit_hash
    else:
        commit = "HEAD"
    # checking user commit hash
    commit_found  = False
    if commit == "HEAD":
        refs = git.cmd.Git().ls_remote(repo_url).split("\n")
        for ref in refs:
            if ref.split('\t')[1] == "HEAD":
                commit_hash = ref.split('\t')[0]
                commit_found = True
    else:
        commit_hash = commit
    # transfer with rsync
    remote_path = os.path.join("neurolibre-test-api:", "DATA", "book-artifacts", user_repo, provider, repo, commit_hash + "*")
    try:
        f = open("/DATA/synclog.txt", "a")
        f.write(remote_path)
        f.close()
        subprocess.check_call(["rsync", "-avR", remote_path, "/"])
    except subprocess.CalledProcessError:
        flask.abort(404)
    # final check
    def run():
        results = book_get_by_params(commit_hash=commit_hash)
        print(results)
        if not results:
            error = {"reason":"404: Could not found the jupyter book build!", "commit_hash":commit_hash, "repo_url":repo_url}
            yield "\n" + json.dumps(error)
            yield ""
        else:
            yield "\n" + json.dumps(results[0])
            yield ""

    return flask.Response(run(), mimetype='text/plain')

# Register endpoint to the documentation
docs.register(api_books_sync_post)

class BinderSchema(Schema):
    repo_url = fields.String(required=True,description="Full URL of the repository submitted by the author.")
    commit_hash = fields.String(required=False,description="Commit hash.")

@app.route('/api/binder/build', methods=['POST'])
@htpasswd.required
@marshal_with(None,code=422,description="Cannot validate the payload, missing or invalid entries.")
@doc(description='Request a binderhub build on the production server for a given repo.hash. Repo must belong to the roboneuro organization.', tags=['Binder'])
@use_kwargs(BinderSchema())
def api_build_post(user,repo_url, commit_hash):
    binderhub_api_url = "https://binder-mcgill.conp.cloud/build/{provider}/{user_repo}/{repo}.git/{commit}"
    repo = repo_url.split("/")[-1]
    user_repo = repo_url.split("/")[-2]
    provider = repo_url.split("/")[-3]
    if provider == "github.com":
        provider = "gh"
    elif provider == "gitlab.com":
        provider = "gl"
    else:
        flask.abort(400)

    if commit_hash:
        commit = commit_hash
    else:
        commit = "HEAD"
    
    # checking user commit hash
    commit_found  = False
    if commit == "HEAD":
        refs = git.cmd.Git().ls_remote(repo_url).split("\n")
        for ref in refs:
            if ref.split('\t')[1] == "HEAD":
                commit_hash = ref.split('\t')[0]
                commit_found = True
    else:
        commit_hash = commit

    # make binderhub and jupyter book builds
    binderhub_request = binderhub_api_url.format(provider=provider, user_repo=user_repo, repo=repo, commit=commit)
    lock_filepath = f"./{provider}_{user_repo}_{repo}.lock"
    if os.path.exists(lock_filepath):
        lock_age_in_secs = time.time() - os.path.getmtime(lock_filepath)
        # if lock file older than 30min, remove it
        if lock_age_in_secs > 1800:
            os.remove(lock_filepath)
    if os.path.exists(lock_filepath):
        binderhub_build_link = """
https://binder-mcgill.conp.cloud/v2/{provider}/{user_repo}/{repo}/{commit}
""".format(provider=provider, user_repo=user_repo, repo=repo, commit=commit)
        flask.abort(409, binderhub_build_link)
    else:
        with open(lock_filepath, "w") as f:
            f.write("")
    # requests builds
    req = requests.get(binderhub_request)
    def run():
        for line in req.iter_lines():
            if line:
                yield str(line.decode('utf-8')) + "\n"
        yield ""

    return flask.Response(run(), mimetype='text/plain')

# Register endpoint to the documentation
docs.register(api_build_post)