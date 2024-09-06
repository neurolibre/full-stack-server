## Starting Neurolibre Celery service

### Prerequisites

* Check that the `redis-server` service is (installed and) running:

```bash
sudo systemctl status redis-server
```

If it is not running, start it:

```bash
sudo systemctl start redis-server
```

The redis server is expected to run on port 6379 (by celery).

* Check that a python virtual environment has been created and the dependencies (`<full-stack-server>/api/requirements.txt`) have been installed to that environment.

### Starting the Celery service

#### Copy over systemd and environment files

```bash
cd <full-stack-server>
sudo cp systemd/neurolibre-celery.service /etc/systemd/system/
sudo cp systemd/default/celery /etc/default/
```

Make sure that the `celery` file has correct permissions after copying:

```bash
sudo chown ubuntu:ubuntu /etc/default/celery
sudo chmod 644 /etc/default/celery
```

_Take a look at the following questions/answers before starting the service:_

##### What is hardcoded in the default/celery environment file?

Well, nothing. You can set any environment variable you want in this file without the need of editing the service file and reloading the daemon.

However, make sure that the variables you set are reflective of the actual environment in which the service will be running. Therefore, make sure to set the variables properly in the `default/celery` file.

##### What is hardcoded in the neurolibre-celery service file?

The neurolibre-celery service has the following hardcoded parameters:

- `EnvironmentFile=/etc/default/celery`
- `WorkingDirectory=/home/ubuntu/full-stack-server/api` (nope, you cannot set this from an environment variable)

the remaining parameters are set in the `/etc/default/celery` file.

##### Why `api/start_celery.sh`?

The `api/start_celery.sh` script is used to start the Celery service **within** the virtual environment. This is needed to be able to access python-installed executables (via `os.system`) that are not in the system's default path (such as `gdown`).

This script can access `${VENV_PATH}` because the neurolibre-celery service file sets the `VENV_PATH` environment variable before running the script. No vodoo.

* Now you can start the service:

```bash
sudo systemctl start neurolibre-celery
```

* Check the status of the service:

```bash
sudo systemctl status neurolibre-celery
```

* Enable the service to start at boot:

```bash
sudo systemctl enable neurolibre-celery
```

##### If the celery service is not starting

To see the logs:

```bash
sudo journalctl -xn 100 -u neurolibre-celery
```

* Make sure that you copied over the environment file properly with the correct parameters.

* If it is complaining about a line of code in any of the python scripts under this repository, it is probably because of the cache.

To clear the cache, simply delete the `__pycache__` folder in the root directory of the repository and restart the service.

```bash
rm -rf <full-stack-server>/api/__pycache__
```

## Starting NeuroLibre Full Stack Server

### Prerequisites

* Check that a python virtual environment has been created and the dependencies (`<full-stack-server>/api/requirements.txt`) have been installed to that environment.

* Ensure that the ubuntu dependencies are installed from the apt.txt file:

```bash
sudo apt-get install -y $(cat <full-stack-server>/api/apt.txt)
```

* Ensure that you placed `.env` file in the `<full-stack-server>/api` directory with the correct parameters. See `<full-stack-server>/api/env_template/env.<server-type>.template` files as examples.

* Ensure that the YAML configuration files are properly set for the server type:

- `<full-stack-server>/api/config/common.yaml` (common parameters for both server types)
- `<full-stack-server>/api/config/<server-type>.yaml` (server type specific parameters)

#### Copy over systemd and environment files

```bash
cd <full-stack-server>
sudo cp systemd/neurolibre-<server-type>.service /etc/systemd/system/
sudo cp systemd/default/neurolibre-server /etc/default/
```

Make sure that the `neurolibre-server` file has correct permissions after copying:

```bash
sudo chown ubuntu:ubuntu /etc/default/neurolibre-server
sudo chmod 644 /etc/default/neurolibre-server
```

> Note: The `/etc/default/neurolibre-server` file is a shared environment file containing parameters for both server types (preview and preprint).

* Now you can start the service:

```bash
sudo systemctl start neurolibre-<server-type>
```

* Check the status of the service:

```bash
sudo systemctl status neurolibre-<server-type>
```

* Enable the service to start at boot:

```bash
sudo systemctl enable neurolibre-<server-type>
```

##### If the server is not starting

To see the logs:

```bash
sudo journalctl -xn 100 -u neurolibre-<server-type>
```

* Make sure that you copied over the environment file properly with the correct parameters.

* Make sure that the **YAML configuration files** for the respective server corresponds to the resources available in your system.

* If you have made any changes to these, make sure to restart the service for your changes to take effect:

```bash
sudo systemctl restart neurolibre-<server-type>
```

* Make sure that you have a `.env` file.

### How to create htpasswd file

```bash
sudo apt-get install apache2-utils
htpasswd -c ~/.htpasswd <username>
```

### Important missing from docs

sudo apt install python3.8-distutils

curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash
export NVM_DIR="$([ -z "${XDG_CONFIG_HOME-}" ] && printf %s "${HOME}/.nvm" || printf %s "${XDG_CONFIG_HOME}/nvm")"
[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"

sudo mkdir -p /var/run/neurolibre
sudo chown ubuntu:www-data /var/run/neurolibre
sudo chmod 770 /var/run/neurolibre