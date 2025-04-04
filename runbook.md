# NeuroLibre Deployment Runbook

## Overview

This runbook provides step-by-step instructions for deploying the NeuroLibre full-stack server application using Docker resources. It covers installation requirements, configuration, startup procedures, and common troubleshooting steps.

## Architecture

The NeuroLibre application consists of the following services:

- **API Server**: Flask-based API service for handling requests
- **Celery Worker**: Background task processing for long-running operations
- **Celery Beat**: Scheduled task executor for periodic jobs
- **Nginx**: Web server and reverse proxy for the API
- **PostgreSQL**: Primary database for application data
- **Redis**: Used for Celery task queue and caching
- **PGAdmin**: Database administration interface (optional)

## Prerequisites

- Docker Engine (version 20.10.0 or later)
- Docker Compose (version 2.0.0 or later)
- Git
- 8GB+ RAM recommended
- 20GB+ free disk space
- Access to network file systems (if configured in the application)

## Environment Setup

### 1. Clone the Repository

```bash
git clone https://github.com/neurolibre/full-stack-server.git
cd full-stack-server
```

### 2. Configure Environment Variables

Create a `.env` file in the root directory with the following variables:

```bash
# Database configuration
POSTGRES_USER=neurolibre
POSTGRES_PASSWORD=your_secure_password
POSTGRES_DB=neurolibre

# PGAdmin configuration (optional)
PGADMIN_EMAIL=admin@example.com
PGADMIN_PASSWORD=your_pgadmin_password

# Zenodo API credentials
ZENODO_API=your_zenodo_api_token
ZENODO_SANDBOX_API=your_zenodo_sandbox_api_token

# GitHub API credentials
GH_BOT=your_github_bot_token

# Data paths
DATA_ROOT_PATH=/DATA
DATA_NFS_PATH=/DATA_NFS

# Optional: Other configuration
ENVIRONMENT=development  # or production
```

### 3. Configure Application Settings

Review and modify the following configuration files if needed:

- `config/common.yaml` - Common configuration settings
- `config/preview.yaml` - Preview environment settings
- `config/preprint.yaml` - Preprint environment settings

Configure authentication:

```
# Install htpasswd utility
sudo apt install apache2-utils -y
# Create .htpasswd file
htpasswd -c api/.htpasswd user_name
```

### 4. SSL Configuration (for Production)

For a production deployment with HTTPS:

```
# Create SSL directory
mkdir -p ssl

# Copy your SSL certificates
cp /path/to/your/certificate.crt ssl/neurolibre.crt
cp /path/to/your/private.key ssl/neurolibre.key

# Set proper permissions
chmod 600 ssl/neurolibre.key
```

## Deployment Steps

### 1. Build and Start Services

```bash
# Build all docker images
docker-compose build

# Start all services in detached mode
docker-compose up -d
```

### 2. Initialize Database

```bash
# Initialize the database
docker-compose up -d postgres
sleep 10
# Run db migrations
docker-compose run --rm api alembic -c api/db/migrations/alembic.ini upgrade head
```

This will start the full stack of services defined in the docker-compose.yml file.

### 3. Start all services

```bash
# Start all services (in production mode)
docker-compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

### 4. Verify Deployment

Check if all containers are running:

```bash
docker-compose ps

# Check logs for any errors
docker-compose logs -f
```

All services should show as "Up" status. Expected containers:
- neurolibre-api
- neurolibre-celery-worker
- neurolibre-celery-beat
- neurolibre-nginx
- neurolibre-postgres
- neurolibre-redis
- neurolibre-pgadmin (if enabled)

### 4. Access the Application

- API server: http://localhost (via Nginx) or https://localhost (if SSL configured)
- PGAdmin interface: http://localhost:5050

## Monitoring and Management

### View Logs

```bash
# View logs from all services
docker-compose logs

# View logs from a specific service
docker-compose logs api
docker-compose logs celery-worker
docker-compose logs celery-beat

# Follow logs in real-time
docker-compose logs -f
```

### Manage Celery Tasks

```bash
# Access Celery worker container
docker-compose exec celery-worker bash

# Check task queue status
celery -A api.celery_tasks inspect active
celery -A api.celery_tasks inspect scheduled

# Purge all queued tasks (use with caution)
celery -A api.celery_tasks purge
```

### Database Management

```bash
# Access PostgreSQL CLI
docker-compose exec postgres psql -U neurolibre -d neurolibre

# Create a database backup
docker-compose exec postgres pg_dump -U neurolibre neurolibre > backup.sql

# Restore a database from backup
cat backup.sql | docker-compose exec -T postgres psql -U neurolibre -d neurolibre
```

## Scaling

To scale the number of Celery workers:

```bash
docker-compose up -d --scale celery-worker=3
```

## Backup and Restore

### Backup Data Volumes

```bash
# Backup PostgreSQL data
docker run --rm --volumes-from neurolibre-postgres -v $(pwd):/backup ubuntu tar cvf /backup/postgres_backup.tar /var/lib/postgresql/data

# Backup Redis data
docker-compose exec redis redis-cli SAVE
docker run --rm --volumes-from neurolibre-redis -v $(pwd):/backup ubuntu tar cvf /backup/redis_backup.tar /data
```

### Restore Data Volumes

```bash
# Restore PostgreSQL data
docker run --rm --volumes-from neurolibre-postgres -v $(pwd):/backup ubuntu bash -c "cd / && tar xvf /backup/postgres_backup.tar"
docker-compose restart postgres

# Restore Redis data
docker run --rm --volumes-from neurolibre-redis -v $(pwd):/backup ubuntu bash -c "cd / && tar xvf /backup/redis_backup.tar"
docker-compose restart redis
```

## Troubleshooting

### Container Fails to Start

1. Check for errors in logs:
   ```bash
   docker-compose logs [service_name]
   ```

2. Verify environment variables are set correctly in the `.env` file

3. Ensure required ports are not already in use by other applications

4. Check disk space and system resources:
   ```bash
   df -h
   free -m
   ```

### Celery Tasks Not Processing

1. Check if Redis is running:
   ```bash
   docker-compose exec redis redis-cli ping
   ```
   Should return "PONG"

2. Restart the Celery worker:
   ```bash
   docker-compose restart celery-worker
   ```

3. Check Celery worker logs for errors:
   ```bash
   docker-compose logs celery-worker
   ```

### API Request Failures

1. Verify the API container is running:
   ```bash
   docker-compose ps api
   ```

2. Check API logs for error messages:
   ```bash
   docker-compose logs api
   ```

3. Check Nginx logs:
   ```bash
   docker-compose logs nginx
   ```

4. Test the database connection:
   ```bash
   docker-compose exec api python -c "from flask import current_app; from api import create_app; app = create_app(); with app.app_context(): print(current_app.db.engine.connect())"
   ```

### Data Volume Issues

If you experience permission issues with mounted volumes:

```bash
# Fix permissions for DATA_ROOT_PATH
sudo chown -R 1000:1000 ${DATA_ROOT_PATH}
```

## Maintenance

### Updating the Application

```bash
# Pull latest changes
git pull

# Rebuild containers with new code
docker-compose build

# Restart services with zero downtime
docker-compose up -d
```

### Cleaning Up

Remove unused Docker resources to free up disk space:

```bash
# Remove stopped containers
docker-compose down

# Remove unused Docker images
docker image prune -a

# Remove unused volumes (careful, this will delete data!)
docker volume prune
```

## Production Considerations

### Security

- Keep your `.env` file secure and never commit it to version control
- Use strong passwords for all services
- Regularly update your Docker images to get security patches
- Set up proper firewall rules to restrict access to services
- Consider using a secrets management solution instead of environment variables

### Performance

- For high-load scenarios, consider using a dedicated Redis instance for caching
- Monitor system resources and adjust container limits as needed
- Configure proper database indexes for frequently accessed data

### Backups

- Set up automated daily backups for all persistent data
- Test backup restoration procedures regularly
- Store backups in multiple locations

## References

- [Docker Documentation](https://docs.docker.com/)
- [Docker Compose Documentation](https://docs.docker.com/compose/)
- [PostgreSQL Documentation](https://www.postgresql.org/docs/)
- [Celery Documentation](https://docs.celeryproject.org/)
- [Redis Documentation](https://redis.io/documentation)
- [Nginx Documentation](https://nginx.org/en/docs/) 