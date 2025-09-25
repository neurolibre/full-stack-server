# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development Environment Setup

### Python Environment
This project uses Python 3.6.9+ with virtual environments. Install dependencies:

```bash
# Create virtual environment
mkdir ~/venv
cd ~/venv
python3 -m venv neurolibre
source ~/venv/neurolibre/bin/activate

# Install dependencies
pip3 install --upgrade pip
pip3 install -r api/requirements.txt
```

### Environment Configuration
Copy the appropriate environment template and configure:
```bash
# For preview server
cp api/env_templates/env.preview.template api/.env

# For preprint server  
cp api/env_templates/env.preprint.template api/.env
```

## Architecture Overview

### Server Types
The system operates two distinct server environments:

1. **Preview Server** (`neurolibre_preview_api.py`) - Handles preview & screening tasks prior to submission
2. **Preprint Server** (`neurolibre_preprint_api.py`) - Handles publishing tasks after technical screening

### Core Components

#### Flask Application Structure
- **Common API** (`neurolibre_common_api.py`) - Shared functionality between servers
- **Server-specific APIs** - Preview and preprint specific endpoints
- **Configuration** - YAML-based config in `api/config/` directory
  - `common.yaml` - Shared configuration
  - `preview.yaml` - Preview server specific
  - `preprint.yaml` - Preprint server specific

#### Key Technologies
- **Flask** - Web framework with API blueprints
- **Gunicorn** - WSGI application server
- **Celery** - Asynchronous task queue with Redis backend
- **NGINX** - Reverse proxy and static file serving
- **Docker** - Containerization for reproducible environments

#### Task Management
Celery handles asynchronous operations:
- Book building (JupyterBook/MyST)
- Data synchronization between servers
- Zenodo archival processes
- Binder image builds

## Development Commands

### Running the Application
```bash
# Activate environment
source ~/venv/neurolibre/bin/activate

# Preview server (development)
python api/neurolibre_preview_api.py

# Preprint server (development)  
python api/neurolibre_preprint_api.py
```

### Celery Worker
```bash
# Start Celery worker
source ~/venv/neurolibre/bin/activate
cd api/
celery -A neurolibre_celery_tasks worker --loglevel=info
```

### Production Deployment
The application runs as systemd services:
- `neurolibre-preview.service` - Preview server
- `neurolibre-preprint.service` - Preprint server
- Celery workers for async tasks

## Key Directories

- `api/` - Main application code
  - `config/` - YAML configuration files
  - `templates/` - HTML templates
  - `env_templates/` - Environment file templates
- `nginx/` - NGINX configuration templates
- `systemd/` - Service configuration files
- `fail2ban/` - Security configuration

## Configuration Notes

### Server Domains
- Preview: `preview.neurolibre.org`
- Preprint: `preprint.neurolibre.org`
- Both use same domain (`neurolibre.org`) with different subdomains

### Data Paths
- Root data path: `/DATA` (shared between servers)
- Book artifacts: `/DATA/book-artifacts`
- MyST sources: `/DATA/myst`
- Logs: `/DATA/logs`

### GitHub Integration
- Uses GitHub API for repository management
- Forked repositories go to `roboneurolibre` organization
- Review issues tracked in `neurolibre/neurolibre-reviews`

## Security Features

- HTTP Basic Authentication for API endpoints
- SSL/TLS with Cloudflare integration
- fail2ban for brute-force protection
- Separate preview/production environments

## Monitoring

- NewRelic integration for infrastructure monitoring
- NGINX status monitoring
- Celery task monitoring
- Comprehensive logging with journalctl