# Deployment

Production deployment guide for **backup-server**. The image is built from
the `Dockerfile` at the repo root; this document covers building it,
running it via `docker compose`, and optionally fronting it with Traefik.

---

## 1. Build the image

```bash
# Apple Silicon / ARM64 Linux:
docker build --build-arg RESTIC_ARCH=arm64 -t backup-server:latest .

# Intel/AMD x86-64:
docker build --build-arg RESTIC_ARCH=amd64 -t backup-server:latest .
```

`RESTIC_ARCH` is **required** — the build fails loudly without it. Only
`arm64` and `amd64` are accepted; anything else is rejected with a clear
error.

`RESTIC_VERSION` defaults to the version pinned in the `Dockerfile`. To
upgrade restic later, pass `--build-arg RESTIC_VERSION=<new>` and rebuild.

Expected final image size: **~165–190 MB**.

---

## 2. Create the host data directory

The container persists SQLite + the restic local cache under `/app/data`.
Pick a directory on the host that will hold this state and that survives
container restarts:

```bash
mkdir -p ./data
```

---

## 3. `docker-compose.yml`

Save this at the deployment directory (e.g. `~/apps/backup-server/docker-compose.yml`).
Adjust the `volumes` block for your actual sources and destinations —
**every backup source must be mounted under `/sources/<label>` read-only**
and **every backup destination must be mounted under `/destinations/<label>`
read-write**. The `<label>` you use here is what shows up in the UI's
mount picker.

```yaml
services:
  backup-server:
    image: backup-server:latest
    container_name: backup-server
    environment:
      - TZ=America/Los_Angeles
      - LOG_LEVEL=INFO
    # ⚠ SECURITY: this port binding bypasses Traefik and any auth middleware.
    # Anyone on the LAN can reach the UI unauthenticated. For production
    # behind Traefik, remove the ports: block entirely and rely on the
    # traefik network. Keep this block only for initial bring-up or
    # troubleshooting.
    ports:
      - '12345:12345'
    volumes:
      # ── Sources (read-only) ── must live under /sources/<label>
      - /Users/yash/Documents:/sources/documents:ro
      - /Volumes/YashNAS:/sources/nas:ro
      # - /Users/yash/Photos:/sources/photos:ro

      # ── Destinations (read-write) ── must live under /destinations/<label>
      - /Volumes/BackupDrive:/destinations/main:rw
      # - /Volumes/BackupDrive2:/destinations/offsite:rw

      # ── App data (SQLite + restic cache) ── do not change this path
      - ./data:/app/data:rw
    restart: unless-stopped
    # Optional: only needed if you front the container with Traefik.
    networks:
      - traefik_default

networks:
  traefik_default:
    external: true
```

If you're not using Traefik, drop the `networks:` block entirely (both
the service-level and top-level entries).

### Environment variables

| Variable    | Default | Purpose                                                                                |
| ----------- | ------- | -------------------------------------------------------------------------------------- |
| `TZ`        | `UTC`   | IANA timezone used for schedule evaluation and timestamp display.                      |
| `LOG_LEVEL` | `INFO`  | Root log level. Set to `DEBUG` to surface every `@log_call`-decorated function's args. |

`RESTIC_CACHE_DIR=/app/data/restic-cache` is set inside the image and
should not be overridden — it must live on the persistent volume so the
cache survives container restarts.

Restic repository passwords are **not** environment variables — each
backup job stores its own password in the SQLite DB, configured via the
UI when the job is created.

### Run it

```bash
docker compose up -d
docker compose logs -f backup-server
```

The first start runs `alembic upgrade head` to materialise the SQLite
schema, then boots `uvicorn` on port 12345. Open the UI at
`http://<host>:12345/` and create your first job.

---

## 4. Traefik integration (optional)

If you already run Traefik with a Cloudflare cert resolver and want
TLS + basic-auth in front of the UI, add the following to your Traefik
dynamic config (commonly `traefik_dynamic.yaml`):

```yaml
http:
  routers:
    backup-server:
      rule: 'Host(`backup-server.example.com`)'
      entryPoints:
        - websecure
      service: backup-server
      tls:
        certResolver: cloudflare
      middlewares:
        - backup-server-auth

  services:
    backup-server:
      loadBalancer:
        servers:
          # Point this at the host IP:port the container is bound to,
          # or use the docker provider so Traefik discovers it via the
          # shared network.
          - url: 'http://192.168.30.11:12345'

  middlewares:
    backup-server-auth:
      basicAuth:
        users:
          # Generate with: htpasswd -nB <username>
          - 'yash:$2y$05$...'
```

Swap `basicAuth` for any access-control middleware you prefer (IP
allowlist, forward auth, OAuth, etc.). Once Traefik is fronting the
service, remove the `ports:` block from `docker-compose.yml` so the
container is only reachable through Traefik.

---

## 5. Upgrading

1. `git pull` to get the latest source.
2. Rebuild: `docker build --build-arg RESTIC_ARCH=<your-arch> -t backup-server:latest .`
3. `docker compose up -d` — Compose recreates the container; Alembic
   migrations apply on boot.
4. SQLite + restic cache + every backup repo are on the persistent
   volume, so no data is lost.

To upgrade restic only (without changing app code):

```bash
docker build --build-arg RESTIC_ARCH=arm64 --build-arg RESTIC_VERSION=0.17.4 -t backup-server:latest .
docker compose up -d
```

---

## 6. Backing up the backup server

The SQLite database at `data/backup.db` contains every job, run, and
snapshot record. The actual restic repositories live under whatever you
mounted to `/destinations/<label>`. To make config + history survive a
catastrophic host loss, periodically copy `data/backup.db` (along with
`data/restic-cache/` if you want to skip rebuilding it) to an off-host
location.
