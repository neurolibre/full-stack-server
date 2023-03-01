NeuroLibre web server that serves both static files and API endpoints. Please see documentation pages for [learning]() about, [deploying]() and [debugging]() this full-stack server component of NeuroLibre ecosystem.

## Learn

### Static files

Static files are the reproducible preprint content (HTML, CSS, JS, etc.) that are generated in one of the following cases:

1. The user front-end of the RoboNeuro web application (https://roboneuro.herokuapp.com/)
2. The technical screening process on the NeuroLibre review repository (https://github.com/neurolibre/neurolibre-reviews/issues)
3. The finalized version of the reproducible preprint.

Cases 1-2 are handled on the `preview` server (on [Compute Canada Arbutus](https://arbutus.cloud.computecanada.ca/) to `preview.conp.cloud`), while case 3 is handled on the `production` server (on [NeuroLibre's own cloud](https://opennebula.conp.cloud) to `preprint.conp.cloud`), both making the respective content available to the public internet.

Under the hood, we use [NGINX](https://docs.nginx.com/nginx/admin-guide/web-server/serving-static-content/) to serve static content. To manage the [DNS records](https://www.cloudflare.com/learning/dns/dns-records/) for the domain `conp.cloud` over which NGINX serves the content, we are using [Cloudflare](https://www.cloudflare.com). Cloudflare also provides [SSL/TLS encryption](https://www.cloudflare.com/learning/ssl/what-is-ssl/) and [CDN](https://www.cloudflare.com/learning/cdn/what-is-a-cdn/) (content delivery network, not Cotes-des-Neiges :), a tiny Montrealer joke).

A good understanding of these concepts is essential for successfully deploying NeuroLibre's reproducible preprints to production. Make sure you have a solid grasp of these concepts before proceeding with the deployment instructions.

### API endpoints

An application programming interface (API) endpoint is a specific location within NeuroLibre server (e.g., `preview.conp.cloud/api/books/all`) that provides access to resources and functionality that are available (e.g., list reproducible preprints on this server):

* Some of the resources and functions available on the `preview` and `production` servers differ. For instance, only the `production` server is responsible for archiving the finalized preprint content on Zenodo, while JupyterBook builds are currently only executed on the `preview` server.

* On the other hand, there are `common` resources and functions shared between the`preview` and `production` servers, such as retrieving a reproducible preprint.

There is a need to reflect this separation between `preview`, `production`, and `common` tasks in the logic of how NeuroLibre API responds to the HTTP requests. To create such a framework, we are using [Flask](https://flask.palletsprojects.com). Our Flask framework is defined by three python scripts: 

```
full-stack-server/
├─ api/
│  ├─ neurolibre_common.py
│  ├─ neurolibre_preview.py
│  ├─ neurolibre_production.py
```

Even though Flask includes a built-in web server that is suitable for development and testing, it is not designed to handle the high levels of traffic and concurrency that are typically encountered in a production environment.

[Gunicorn](https://gunicorn.org/), on the other hand, is a production-grade application server that is designed to handle large numbers of concurrent tasks. It acts as a web service gateway interface (WSGI) that knows how to talk Python to Flask. As you can infer by its name, it is an "interface" between Flask and something else that, unlike Gunicorn, knows how to handle web traffic.

That something else is a [reverse proxy server](https://docs.nginx.com/nginx/admin-guide/web-server/reverse-proxy/), and you already know its name, NGINX! It is the gatekeeper of our full-stack web server. NGINX decides whether an HTTP request is made for static content or the application logic (encapsulated by Flask, served by Gunicorn).

I know you are bored to death, so I tried to make this last bit more fun:

This Flask + Gunicorn + NGINX trio plays the music we need for a production-level NeuroLibre full-stack web server. Of these 3, NGINX and Gunicorn always have to be all ears to the requests coming from the audience. In more computer sciency terms, they need to have their own [daemons](https://en.wikipedia.org/wiki/Daemon_(computing)) 👹.

NGINX has its daemons, but we need a unix systemD (d for daemon) ritual to summon deamons upon Gunicorn 🕯👹👉🦄🕯. To do that, we need go to the `/etc/` dungeon of our ubuntu virtual machine and drop a service spell (`/systemd/neurolibre.service`). This will open a portal (a unix socket) through which Gunicorn's deamons can listen to the requests 24/7. We will tell NGINX where that socket is, so that we can guide right mortals to the right portals.

Let's finish the introductory part of our full-stack web server with reference to [this Imagine Dragons song](https://www.youtube.com/watch?v=mWRsgZuwf_8):

```
  When the kernels start to crash
  And the servers all go down
  I feel my heart begin to race
  And I start to lose my crown

  When you call my endpoint, look into systemd
  It's where my daemons hide
  It's where my daemons hide
  Don't get too close, /etc is dark inside
  It's where my daemons hide
  It's where my daemons hide

  I see the error messages flash
  I feel the bugs crawling through my skin
  I try to debug and fix the code
  But the daemons won't let me win (you need sudo)
```

P.S. No chatGPT involved here, only my demons.

### Security

On Cloudflare, we activate [full(strict)](https://developers.cloudflare.com/ssl/origin-configuration/ssl-modes/full-strict/) encryption mode for handling SSL/TLS certification. In addition, we disable legacy TLS versions of  `1.0` and `1.1` due to [known vulnerabilities](https://www.acunetix.com/blog/articles/tls-vulnerabilities-attacks-final-part/). With these configurations, we receive a solid SSL Server Rating of A from [SSL Labs](https://www.ssllabs.com/ssltest/analyze.html).

While implementing SSL is a fundamental necessity for the security of our server, it is not sufficient on its own. SSL only addresses the security of the communication channel between a website and its users, and does not address other potential security vulnerabilities. For example, any web server will be subjected to brute-force attacks typically coming from large botnets. To deal with this, we are using `fail2ban`, which is a tool that monitors our nginx log files and bans IP addresses that show malicious activity, such as repeated failed login attempts.

#### What else? - Future considerations 

Another consideration is client-side certificate authorization. In this approach, clients (e.g., `roboneuro`) are required to present a digital certificate as part of the authentication process when they attempt to access a server or service. The server then verifies the certificate to determine whether the client is authorized to access the requested resource. This would require creating a client certificate on Cloudflare, then adding that to the server block :

```
ssl_client_certificate  /etc/nginx/client-ca.crt;
ssl_verify_client optional;
```

Verification must be location-optional, as it works against serving static files. To achieve this only for api endpoints, the config would look like this:

```
location /api/ {
...
if ($ssl_client_verify != "SUCCESS") { return 403; }
...
}
```

This is currently NOT implemented due to potential issues on Heroku, where our web apps are hosted. 

Alternatively, Cloudflare provides [API Shield](https://developers.cloudflare.com/api-shield/) for enterprise customers and [mutual TLS](https://developers.cloudflare.com/api-shield/security/mtls/) for anyone.

### Performance

<details>
  <summary>Expand this tab to see the list of key configurations that determine the performance of serving static files with nginx</summary>
  <ul>
    <li><code>worker_processes</code>: This directive specifies the number of worker processes that nginx should use to handle requests. By default, nginx uses one worker process, but you can increase this number if you have a multi-core system and want to take advantage of multiple cores.</li>
    <li><code>worker_connections</code>: This directive specifies the maximum number of connections that each worker process can handle. Increasing this value can improve the performance of nginx if you have a high number of concurrent connections.</li>
    <li><code>sendfile</code>: This directive enables or disables the use of the <code>sendfile()</code> system call to send file contents to clients. Enabling <code>sendfile</code> can improve the performance of nginx when serving large static files, as it allows the kernel to copy the data directly from the filesystem cache to the client without involving the worker process.</li>
    <li><code>tcp_nopush</code>: This directive enables or disables the use of the <code>TCP_NOPUSH</code> socket option, which can improve the performance of nginx when sending large responses to clients by allowing the kernel to send multiple packets in a single batch.</li>
    <li><code>tcp_nodelay</code>: This directive enables or disables the use of the <code>TCP_NODELAY</code> socket option, which can improve the performance of nginx by disabling the Nagle algorithm and allowing the kernel to send small packets as soon as they are available, rather than waiting for more data to be buffered.</li>
    <li><code>gzip</code>: This directive enables or disables gzip compression of responses. Enabling gzip compression can improve the performance of nginx by reducing the amount of data that needs to be transmitted over the network.</li>
    <li><code>etag</code>: This directive enables or disables the use of <code>ETag</code> headers in responses. Enabling <code>ETag</code> headers can improve the performance of nginx by allowing clients to cache responses and reuse them without making additional requests to the server.</li>
    <li><code>expires</code>: This directive sets the <code>Expires</code> header in responses, which tells clients to cache responses for a specified period of time. Enabling <code>Expires</code> headers can improve the performance of nginx by allowing clients to reuse cached responses without making additional requests to the server.</li>
    <li><code>keepalive_timeout</code>: This directive sets the timeout for keepalive connections, which allows clients to reuse connections for multiple requests. Increasing the value of <code>keepalive_timeout</code> can improve the performance of nginx by reducing the overhead of establishing new connections.</li>
    <li><code>open_file_cache</code>: This directive enables file caching, which can improve the performance of nginx by allowing it to reuse previously opened files rather than opening them anew for each request.</li>
  </ul>
</details>

For further details on tuning NGINX for performance, see these blog posts about [optimizing nginx configuration](https://www.nginx.com/blog/tuning-nginx/) and [load balancing](https://www.nginx.com/blog/load-balancing-with-nginx-plus).

You can use [GTMetrix](https://gtmetrix.com/) to test the loading speed of individual NeuroLibre preprints. The loading speed of these pages mainly depends on the content of the static files they contain. For example, pages with interactive plots rendered using HTML may take longer to load because they encapsulate all the data points for various UI events.

## Deploy and configure NeuroLibre servers

Clone this repository to `home` directory (typically `/home/ubuntu`):

```
cd ~
git clone https://github.com/neurolibre/full-stack-server.git
```

Be careful not to run these commands (or anything else in this section) as the `root` user. If you ssh'd into the VM as root, you can switch to ubuntu by executing the `su ubuntu` command in the remote terminal. 

> Throughout the rest of this section, **`<type>`** refers to either `preview` or `preprint`.

#### Flask, Gunicorn and other Python dependencies 

This documentation assumes that the server host is a Ubuntu VM. To install Python dependencies, 
we are going to use virtual environments.

Ensure that python3 (3.6.9 or later) is available: 

```
which python3
```

Install `virtualenv` by:

```
sudo apt install python3-venv
```
Create a new folder (`venv`) under the home directory and inside that folder, create a virtual environment named `neurolibre`:

```
mkdir ~/venv
cd ~/venv
python3 -m venv neurolibre
```

> Note: Please do not replace the virtual environment name above (`neurolibre`) with something else. You can take a look at the `systemd/neurolibre-<type>.service` configuration files as to why. 

If successful, you should see `~/venv/neurolibre` created. Now, activate this virtual environment to the install dependencies in the right place:

```
source ~/venv/neurolibre/bin/activate
```

If successful, the name of the environment should appear on bash, something like `(neurolibre) ubuntu@neurolibre-sftp:~/venv$`. **Ensure that the (neurolibre) environment is activated when you are executing the following commands**:

```
pip3 install --upgrade pip
pip3 install -r ~/full-stack-server/api/requirements.txt
```

You can confirm the packages/versions via `pip3 freeze`.

##### Add working environment secret variables 

Based on the `env.<type>.template` file located at the `/api/` folder under this repository (`~/full-stack-server/api`). create a `~/full-stack-server/api/.env` file and fill out the respective variable values:

```
cp ~/full-stack-server/api/env.<type>.template ~/full-stack-server/api/.env
nano .env
```

> Note, this file will be ignored by git as it MUST NOT be shared. Please ensure that the file name is correct (`~/full-stack-server/api/.env`).

#### Configure the server as a systemd service

Depending on the server **type** [`preview` or `preprint`], copy the respective content from `sytemd` folder in this repository into `/etc/systemd/system`:

```
sudo cp ~/full-stack-server/systemd/neurolibre-<type>.service /etc/systemd/system/neurolibre-<type>.service
```

If the python virtual environment and its dependencies are properly installed, you can start the service by: 

```
sudo systemctl start neurolibre-<type>.service
```

You can check the status by 

```
sudo systemctl status neurolibre-<type>.service
```

This should start multiple `gunicorn` workers, each one of them binding our flask application to a `unix socket` located at `~/full-stack-server/api/neurolibre_<type>_api.sock`. You can check the existence of the `*.sock` file at this directory. The presence of this socket file is of key importance as in the next step, we will register it to nginx as an upstream server! 

> Reminder: Replace the **`<type>`** in the commands above either with `preprint` or `preview` depending on the server (e.g., `neurolibre-preview.service`) you are configuring. Note that this is not only a naming convention, but also defines a functional separation between the roles of the two servers.

#### Preprint <--> Preview serve data sync configurations

After technical screening process, the final version of the Jupyter Book and respective data will be transferred from the preview (source) to the preprint (destination) server. At least as for the current convention. To achieve this, we preferred [`rsync`](https://linux.die.net/man/1/rsync) that uses ssh for communication between the source and destination.

Whenever the public IP of either server changes and/or the VMs are re-spawned from scratch, please ensure that the following configuration is valid.  

1. Create an ssh keypair _on the destination (preprint) server_ `ssh-keygen -t rsa`
2. Add the **public** key (`*.pub`) to the `~/.ssh/authorized_keys` file _in the source (preview) server_. This will allow production server to pull files from the preview server.
3. Confirm that you can ssh **into** the source (preview) server **from** the destination (preprint) server `ssh -i ~/.ssh/key ubuntu@preview.server.ip`.
4. Create an ssh configuration file `~/.ssh/config` _on the destination (preprint) server_ to recognize preview (source) server as a host. The content of the configuration will be:

```
Host neurolibre-preview
        HostName xxx.xx.xx.xxx
        User ubuntu
```

Ensure that the you replaced `xxx.xx.xx.xxx` with the public IP address of the preview server. The first line of the configuration above declares the alias `neurolibre-preview`. If you change this name, you will need to make respective changes in the `neurolibre_preprint_api.py`. 

5. Test file transfer. SSH into the destination (preprint) server and pull an example file from the source server: 

```
rsync -avR neurolibre-preview:/DATA/foo.txt /
```

Provided that the `/DATA/foo.txt` exists on the source (preview) server and you successfully configured ssh, you should see the same file appearing at the same destination (directory syncing, see more [here](https://www.digitalocean.com/community/tutorials/how-to-use-rsync-to-sync-local-and-remote-directories)) on the destination (preprint) server.

#### Cloud-level considerations



#### NGINX installation and configurations

To install and configure `nginx`: 

```
sudo apt install nginx
```

Allow HTTP (80) and HTTPS (443) ports:

```
sudo ufw allow 80,443/tcp
```

Create the following folders: 

```
sudo mkdir /etc/nginx/sites-available
sudo mkdir /etc/nginx/sites-enabled
```

##### Update the `nginx.conf` and add `neurolibre_params`

Replace the default nginx configuration file with the one from this repository:

```
sudo cp ~/full-stack-server/nginx/nginx.conf /etc/nginx/nginx.conf
```

Add proxy pass parameters for the upstream server that is gunicorn/flask:

```
sudo cp ~/full-stack-server/nginx/neurolibre_params /etc/nginx/neurolibre_params
```

##### Add server-specific configuration files

Depending on the server **type** [`preprint` or `preview`], copy `/nginx/neurolibre-<type>.conf` file to `/etc/nginx/sites-available`:

```
sudo cp ~/full-stack-server/nginx/neurolibre-<type>.conf /etc/nginx/sites-enabled/neurolibre-<type>.conf
```

> Reminder: Replace the **`<type>`** in the commands above either with `preprint` or `preview` depending on the server (e.g., `neurolibre-preview.service`) you are configuring.

##### Create SSL certificates

* Login to the cloudflare account, got to the respective site domain (e.g. `conp.cloud` or `neurolibre.org`), under the `SSL/TLS` --> `Origin Server` --> `Create Certificate`.

* Use the default method (RSA 2048), leave the host names as is (or define a single subdomain, your call). Click create. 

* This will create two long strings, one for `certificate` (first) and one for the private `key`. Create two files under `/etc/ssl` directory:
     *  `cd /etc/ssl`
     * `sudo nano /etc/ssl/conp.cloud.pem` --> Copy the `certificate` key here and save 
     * `sudo nano /etc/ssl/conp.cloud.key` --> Copy the `key` string here and save.

> Note: `conp.cloud.pem` and `conp.cloud.key` can be changed with any alternative name, such as `neurolibre.pem` and `neurolibre.key` as long as the origin certificate content is accurate AND if your `nginx.conf` is configured to look for that new file name:

```
    ssl_certificate     /etc/ssl/conp.cloud.pem;
    ssl_certificate_key    /etc/ssl/conp.cloud.key;
```

Remember that the same directives also exist in the `/etc/nginx/sites-available/neurolibre-<type>.conf` configuration files, both for `preview` and `preprint`. If you decide to change the certificate name, you will need to update these configs as well.

##### A tiny hack to serve swagger ui static assets over upstream 

This is a bit tricky both because a funny `_` (what python gives) vs `-` (what nginx expects) mismatch, also because we will be serving the swagger UI over a convoluted path. When you run the flask app locally, it will know where to locate UI-related assets and serve the UI on your localhost. But when we attempt it from `https://<type>.neurolibre.org/documentation`, our NGINX server will not be able to locate them, so we help it: 

```
sudo mkdir /etc/nginx/html/flask-apispec
sudo cp -r ~/venv/neurolibre/lib/python3.6/site-packages/flask_apispec/static /etc/nginx/html/flask-apispec/.
```

This is required for both server types.

##### Start the server

When you symlink the configuration file from `sites-available` to `sites-enabled`, it will take effect: 

```
sudo ln -s /etc/nginx/sites-available/neurolibre-<type>.conf /etc/nginx/sites-enabled/neurolibre-<type>.conf
```

then

```
sudo systemctl restart nginx
```

That's it! The server should be accessible at the domain you configured (e.g. https://preview.neurolibre.org)

This is required for both server types.

> Remember to use the correct name (`neurolibre-<type>.conf`) for the respective (`preprint` or `preview`) server you are configuring.

> Also, if your upstream server, i.e. the gunicorn socket, is not active, the webpage will not load. Ensure that `sudo systemctl status neurolibre-<type>.service` shows active status for the respective server. 

### Newrelic installation

We will deploy New Relic Infrastructure (`newrelic-infra`) and the NGINX integration for New Relic (`nri-nginx`,[source repo](https://github.com/newrelic/nri-nginx)) to monitor the status of our host virtual machine (VM) and the NGINX server. 

With these tools, we will be able to track the performance and availability of our host and server, and identify and troubleshoot any issues that may arise. By using New Relic and the NGINX integration, we can manage and optimize the performance of our system from a single location.

> You need credentials to login to [NewRelic portal](https://one.newrelic.com/). Otherwise you cannot proceed with the installation and monitoring. 

Ssh into the VM (`ssh -i ~/.ssh/your_key root@full-stack-server-ip-address`) and follow these instructions:

1. Install new relic infrastructure agent 

After logging into the newrelic portal, click `+ add data`, then type `ubuntu` in the search box. Under the `infrastructure & OS`, click `Linux`. When you click the `Begin installation` button, the installation command with proper credentials will be generated. Simply copy/paste and execute that command on the VM terminal.  

Alternatively, you can replace `<NEWRELIC-API-KEY-HERE>` and `<NEWRELIC-ACCOUNT-ID-HERE>` with the respective content below (please do not include the angle brackets).

```bash
curl -Ls https://download.newrelic.com/install/newrelic-cli/scripts/install.sh | bash && sudo NEW_RELIC_API_KEY=<NEWRELIC-API-KEY-HERE> NEW_RELIC_ACCOUNT_ID=<NEWRELIC-ACCOUNT-ID-HERE> /usr/local/bin/newrelic install
```

After successful installation, the newrelic agent should start running. Confirm its status by:

```bash
sudo systemctl status newrelic-infra.service
```
If the installer prompted you to add additional packages including `NGINX`, `Golden Signal Alerts` etc. , you may skip the step 2. below. Nevertheless, go through the second bullet point (of step 2) to confirm successful `nri-nginx` installation.  

2. Install new relic nginx integration

* Download the `nri-nginx_*_amd64.deb` from the assets of the latest (or a desired) [nri-nginx release](https://github.com/newrelic/nri-nginx/releases). You can get the download link by right clicking the respective release asset:

```
wget https://github.com/newrelic/nri-nginx/releases/download/v3.2.5/nri-nginx_3.2.5-1_amd64.deb -O ~/nri-nginx_amd64.deb
```

* Install the package

```
cd ~
sudo apt install ./nri-nginx_amd64.deb
```

* If the installation is successful, you should see `nginx-config.yaml.sample` upon: 

```
ls /etc/newrelic-infra/integrations.d
```

For the next step, confirm that the `stab_status` of the nginx is properly exposed to `127.0.0.1/status` by:

```bash
curl 127.0.0.1/status
```

The output should look like:

```
Active connections: 1 
server accepts handled requests
 126 126 125 
Reading: 0 Writing: 1 Waiting: 0 
```

3. Configure the nginx agent

We will use the default configuration provided in the sample configuration by copying it to a new file:

```
cd /etc/newrelic-infra/integrations.d
sudo cp nginx-config.yml.sample nginx-config.yml
```

This action will start the `nri-nginx` integration. Run `sudo systemctl status newrelic-infra.service` to confirm successful. You should see the _"Integration health check finished with success"_ message for _integration_name=nri-nginx_.

### Fail2ban installation and configuration 

* Install 

```
sudo apt-get install fail2ban
``` 

* Copy over fail2ban configurations from this repository to where they should be:

```
sudo cp -R ~/full-stack-server/fail2ban/* /etc/fail2ban
```

> Note that these configurations assume that `/home/ubuntu/nginx-error.log` and `/home/ubuntu/nginx-access.log` are where they should be and configured as error/access logs for the nginx server.

* Activate NGINX jails:

```
sudo systemctl restart fail2ban.service
```

* Confirm that the service is ready:

```
sudo systemctl status fail2ban.service
```

* See the number and the list of jails set up:

```
sudo fail2ban-client status
```

* Be a responsible guardian and take a look at those jails. For example, to see the list of blocked IPs due to suspicious authentication retries:

```
sudo fail2ban-client status nginx-http-auth
```

You can check other jails (e.g., `nginx-noproxy`,`nginx-nonscript`,`sshd`). 

* Use your get out of the jail card: 

In case you trapped yourself while testing if the jail works:

```
sudo fail2ban-client set nginx-http-auth unbanip ip.address.goes.here
```

See this [documentation](https://www.digitalocean.com/community/tutorials/how-to-protect-an-nginx-server-with-fail2ban-on-ubuntu-14-04) for further details regarding the configurations.

## Monitor, Debug, and Improve

### Use Newrelic

Login to the NewRelic portal where you can take a look at all the entities from both `preview` and `preprint` server. These entities could be specific to NGINX or the hosts events. You can take a look at a variety of logs, and see if there are any errors or critical warnings thrown.

NewRelic not only provides centralized monitoring of multiple resources, but also allows you to set alert conditions! You can install the mobile application to your iPhone/Android and get immediately notified when things are out of control.

### Know your logs 

We have several `systemd` services that are critical. You can use `journalctl` to take a look at what's going on with each one of them. For example, if you need to take a look at the logs from gunicorn (through which Flask logs are forwarded): 

```
journalctl -xn 20 -u neurolibre-preview.service
```

The above would help you understand what went wrong if the service failed to restart. Note that `sudo systemctl status neurolibre-preview.service` is not going to explain what went wrong at the level you expect.

Here, `-xn` is the number of last N lines of log with application context and `-u` is followed by the name of the service (e.g., nginx.service). For further details, see [journalctl reference](https://www.freedesktop.org/software/systemd/man/journalctl.html).

## Dokku

TODO: Move this elsewhere in the documentation.

* Create a Ubuntu VM and associate a floating IP (vm.floating.ip).

* Install Dokku to the VM by following [debian installation instructions](https://dokku.com/docs/getting-started/install/debian/).

* Add an shh key to the Dokku. To achieve this, you need root access (`sudo -i`). If an ssh keypair does not exist (~/.ssh/id_rsa), create one (`ssh-keygen`): 

```
dokku ssh-keys:add <name> ~/.ssh/id_rsa 
```

* Add global domain

```
dokku domains:add-global dashboards.neurolibre.org
```

* On Cloudflare, add an A record for wildcard nested domain `*.dashboards.neurolibre.org`. This will require total TLS to issue individual certificates for every proxied hostname (paid feature). Otherwise, SSL termination will fail.

* On Cloudflare, create origin certificates for the wildcard nested domain, copy over files to a `neurolibre.crt` and `neurolibre.key` files, respectively. Then: 

```
tar cvf neurolibre.tar neurolibre.crt neurolibre.key
```

* We can deploy the first application. Clone a compatible repository, e.g., `my-dashboard` and cd into it: 

```
cd ~/my-dashboard
dokku apps:create my-dashboard
```

> If you app needs, you'll need to create [service plugins](https://dokku.com/docs/community/plugins/#official-plugins-beta) at this step.

* Add git remote for your application: 

```
git remote add dokku dokku@[vm.floating.ip]:my-dashboard
git push dokku main:master 
```

If the main branch is `main`, use `master:master` otherwise, or `branch:master` if that's what you need.

* The push command above will start the deployment. If the VHOST is not enabled by default, you may not see URL being printed at the end of the deployment. In either case, add domain for the app: 

```
dokku domains:add my-dashboard dashboards.neurolibre.org
```

Confirm that it is enabled with: 

```
dokku domains:report my-dashboard
```

enable otherwise: 

```
dokku domains:enable my-dashboard
```

* Add certificates to the application: 

```
dokku add:certificate my-dashboard < ~/neurolibre.tar
```

* That's it! If successful, the app should be live on https://my-dashboard.dashboards.neurolibre.org 

> Needless to say, `my-dashboard` here is just an example name. The repository you'll clone should have basic requirements (e.g., source code, a procfile to indicate what to execute and runtime dependency declarations such as requirements.txt, Gemfile, package.json, pom.xml, etc.) to deploy itself as an application to dokku.

* Each application on Dokku will run in a container, named as "dynos" in Heroku. If you connect this VM to NewRelic (see instructions above), you can monitor each container/application/load and set alert conditions. 