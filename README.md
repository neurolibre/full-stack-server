## About 

NeuroLibre web server that serves both static files and API endpoints.

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
├─ public/
│  ├─ neurolibre_common.py
│  ├─ neurolibre_preview.py
│  ├─ neurolibre_production.py
```

Even though Flask includes a built-in web server that is suitable for development and testing, it is not designed to handle the high levels of traffic and concurrency that are typically encountered in a production environment.

[Gunicorn](https://gunicorn.org/), on the other hand, is a production-grade application server that is designed to handle large numbers of concurrent tasks. It acts as a web service gateway interface (WSGI) that knows how to talk Python to Flask. As you can infer by its name, it is an "interface" between Flask and something else that, unlike Gunicorn, knows how to handle web traffic.

That something else is a [reverse proxy server](https://docs.nginx.com/nginx/admin-guide/web-server/reverse-proxy/), and you already know its name, NGINX! It is the gatekeeper of our full-stack web server. NGINX decides whether an HTTP request is made for static content or the application logic (encapsulated by Flask, served by Gunicorn).

I know you are bored to death, so I tried to make this last bit more fun:

This Flask + Gunicorn + NGINX trio plays the music we need for a production-level NeuroLibre full-stack web server. Of these 3, NGINX and Gunicorn always have to be all ears to the requests coming from the audience. In more computer sciency terms, they need to have their own [daemons](https://en.wikipedia.org/wiki/Daemon_(computing)) 👹.

NGINX has its daemons, but we need a unix systemD (d for daemon) ritual to summon deamons upon Gunicorn 🕯👹👉🦄🕯. To do that, we need go to the `/etc/` dungeon of our ubuntu virtual machine and drop a service spell. This will open a portal (a unix socket) through which Gunicorn's deamons can listen to the requests 24/7. We will tell NGINX where that socket is, so that we can guide right mortals to the right portals.

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