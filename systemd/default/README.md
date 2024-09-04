## This file is used to configure the Celery service.

It should be placed in the /etc/default directory.

### Environment variables

- `CELERYD_NODES`: The nodes to start.
- `CELERY_BIN`: The path to the Celery binary.
- `CELERY_APP`: The name of the Celery app.
- `CELERYD_MULTI`: The multi command.
- `CELERYD_OPTS`: The options to pass to the Celery command.
- `CELERYD_PID_FILE`: The path to the PID file.
- `CELERYD_LOG_FILE`: The path to the log file.
- `CELERYD_LOG_LEVEL`: The log level.

### Notes

- Make sure to set the correct permissions for the `start_celery.sh` script.

  ```bash
  chmod +x /home/ubuntu/full-stack-server/api/start_celery.sh
  ```

- sudo chown ubuntu:ubuntu /etc/default/celery
- sudo chmod 644 /etc/default/celery

Remember to reload the systemd daemon after making changes to the service files.

```bash
sudo systemctl daemon-reload
```

If you encounter errors, check the status of the service with:

```bash
sudo systemctl status <service-name>
```

And check the logs with:

```bash
sudo journalctl -u <service-name> -f
```