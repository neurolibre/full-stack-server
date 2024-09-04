## Starting Neurolibre Celery service

### Prerequisites

Check that the `redis-server` service is (installed and) running:

```bash
sudo systemctl status redis-server
```

If it is not running, start it:

```bash
sudo systemctl start redis-server
```

The redis server is expected to run on port 6379 (by celery).

### Starting the Celery service

#### Copy over systemd and environment files

```bash
sudo cp <full-stack-server>/api/systemd/neurolibre-celery.service /etc/systemd/system/
sudo cp <full-stack-server>/api/systemd/default/celery /etc/default/
```

Make sure that the `neurolibre-server` file has correct permissions after copying:

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