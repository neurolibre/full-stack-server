import os
import requests
import json
from common import *

def zenodo_create_bucket(title, archive_type, creators, user_url, fork_url, commit_user, commit_fork, issue_id):
    ZENODO_TOKEN = os.getenv('ZENODO_API')
    headers = {"Content-Type": "application/json",
                    "Authorization": "Bearer {}".format(ZENODO_TOKEN)}
    
    libre_text = f'<a href="{fork_url}/commit/{commit_fork}"> reference repository/commit by roboneuro</a>'
    user_text = f'<a href="{user_url}/commit/{commit_user}">latest change by the author</a>'
    review_text = f'<p>For details, please visit the corresponding <a href="https://github.com/neurolibre/neurolibre-reviews/issues/{issue_id}">NeuroLibre technical screening.</a></p>'
    sign_text = '\n<p><strong><a href="https://neurolibre.org" target="NeuroLibre">https://neurolibre.org</a></strong></p>'

    data = {}
    data["metadata"] = {}
    data["metadata"]["title"] = title
    data["metadata"]["creators"] = creators
    data["metadata"]["keywords"] = ["canadian-open-neuroscience-platform","neurolibre"]
    # (A) NeuroLibre artifact is a part of (isPartOf) the NeuroLibre preprint (B 10.55458/NeuroLibre.issue_id)
    data["metadata"]["related_identifiers"] = [{"relation": "isPartOf","identifier": f"10.55458/neurolibre.{'%05d'%issue_id}","resource_type": "publication-preprint"}]
    data["metadata"]["contributors"] = [{'name':'NeuroLibre, Admin', 'affiliation': 'NeuroLibre', 'type': 'ContactPerson' }]

    if (archive_type == 'book'):
        data["metadata"]["upload_type"] = 'publication'
        data["metadata"]["publication_type"] = 'preprint'
        data["metadata"]["description"] = 'NeuroLibre JupyterBook built at this ' + libre_text + ', based on the ' + user_text + '.' + review_text + sign_text
    elif (archive_type == 'data'):
        data["metadata"]["upload_type"] = 'dataset'
        data["metadata"]["description"] = 'Dataset provided for NeuroLibre preprint.\n' + f'Author repo: {user_url}\nNeuroLibre fork:{fork_url}' + review_text + sign_text
    elif (archive_type == 'repository'):
        data["metadata"]["upload_type"] = 'software'
        data["metadata"]["description"] = 'GitHub archive of the ' + libre_text + ', based on the ' + user_text + '.' + review_text + sign_text
    elif (archive_type == 'docker'):
        data["metadata"]["upload_type"] = 'software'
        data["metadata"]["description"] = 'Docker image built from the ' + libre_text + ', based on the ' + user_text + f", using repo2docker (through BinderHub). <br> To run locally: <ol> <li><pre><code class=\"language-bash\">docker load < DockerImage_10.55458_NeuroLibre_{'%05d'%issue_id}_{commit_fork[0:6]}.zip</code><pre></li><li><pre><code class=\"language-bash\">docker run -it --rm -p 8888:8888 DOCKER_IMAGE_ID jupyter lab --ip 0.0.0.0</code></pre> <strong>by replacing <code>DOCKER_IMAGE_ID</code> above with the respective ID of the Docker image loaded from the zip file.</strong></li></ol>" + review_text + sign_text

    # Make an empty deposit to create the bucket 
    r = requests.post('https://zenodo.org/api/deposit/depositions',
                headers=headers,
                data=json.dumps(data))
    if not r:
        return {"reason":"404: Cannot create " + archive_type + " bucket.", "commit_hash":commit_fork, "repo_url":fork_url}
    else:
        return r.json()

def docker_login():
    uname = os.getenv('DOCKER_USERNAME')
    pswd = os.getenv('DOCKER_PASSWORD')
    resp = os.system(f"echo {pswd} | docker login {DOCKER_REGISTRY} --username {uname} --password-stdin")
    return resp

def docker_logout():
    resp=os.system(f"docker logout {DOCKER_REGISTRY}")
    return resp

def docker_pull(image):
    resp = os.system(f"docker pull {image}")
    return resp

def docker_export(image,issue_id,commit_fork):
    save_name = os.path.join(get_archive_dir(issue_id),f"DockerImage_10.55458_NeuroLibre_{'%05d'%issue_id}_{commit_fork[0:6]}.tar.gz")
    resp=os.system(f"docker save {image} | gzip > {save_name}")
    return resp, save_name

def get_archive_dir(issue_id):
    path = f"/DATA/zenodo/{'%05d'%issue_id}"
    if not os.path.exists(path):
        os.makedirs(path)
    return path

def get_deposit_dir(issue_id):
    path = f"/DATA/zenodo_records/{'%05d'%issue_id}"
    if not os.path.exists(path):
        os.makedirs(path)
    return path
    # docker rmi $(docker images 'busybox' -a -q)

def lel():
    print('lel')