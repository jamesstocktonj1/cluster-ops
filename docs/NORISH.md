# Norish

Continues from [CLOUDFLARE.md](CLOUDFLARE.md) (the tunnel) and
[STORAGE.md](STORAGE.md) (Longhorn holds the data). Runs the Norish recipe manager,
moved over from docker compose. Reachable only through the Cloudflare Tunnel, behind
Cloudflare Access, at `https://norish.jamesstockton.com`. It has no LAN address.

## How it works

```
browser -> Cloudflare Access -> tunnel -> cloudflared -> norish (ClusterIP :80)
                                                           -> postgres  (Longhorn 5Gi)
                                                           -> valkey    (Longhorn 1Gi)
                                                           -> obscura   (headless browser, stateless)
                                                    uploads: Longhorn 5Gi
```

One file per compose service in [infrastructure/norish/](../infrastructure/norish/),
deployed by [apps/norish.yaml](../apps/norish.yaml). The compose `db_data`,
`valkey_data` and `norish_data` volumes became Longhorn volumes.

## Deploy

1. Create the namespace and the Secret. The Secret is not in git (same as the
   Cloudflare token). Generate new values, don't reuse the compose placeholders:

   ```sh
   kubectl create namespace norish
   kubectl -n norish create secret generic norish-secrets \
     --from-literal=MASTER_KEY="$(openssl rand -base64 32)" \
     --from-literal=OBSCURA_CDP_TOKEN="$(openssl rand -hex 32)" \
     --from-literal=POSTGRES_PASSWORD="$(openssl rand -hex 16)"
   ```

   **If you want to keep your existing data, use your existing `MASTER_KEY`**
   (Norish encrypts stored secrets with it) rather than a new one.

2. Commit and push; Argo CD creates the `norish` app. Check it:

   ```sh
   kubectl -n norish get pods,pvc,svc
   ```

3. Add the public hostname to the `stack` tunnel in the Cloudflare dashboard
   (see [CLOUDFLARE.md](CLOUDFLARE.md)):

   | Field     | Value                                    |
   |-----------|------------------------------------------|
   | Subdomain | `norish`                                 |
   | Domain    | `jamesstockton.com`                      |
   | Type      | HTTP                                     |
   | URL       | `norish.norish.svc.cluster.local:80`     |

   Then add `norish.jamesstockton.com` to a Cloudflare Access application so it
   isn't open to the internet, and open <https://norish.jamesstockton.com>.

## Moving your existing data

Optional. Do this before using the new instance, with the new pods running:

```sh
# database: dump from compose, load into the cluster
docker exec norish-db pg_dump -U postgres norish > norish.sql
kubectl -n norish exec -i postgres-0 -- psql -U postgres norish < norish.sql

# uploads
docker cp norish-app:/app/uploads ./uploads
kubectl -n norish cp ./uploads $(kubectl -n norish get pod -l app=norish -o name | cut -d/ -f2):/app/
```

Then restart Norish: `kubectl -n norish rollout restart deploy/norish`.

## Things to know

- **`AUTH_URL`** is the address the browser uses, set in
  [norish.yaml](../infrastructure/norish/norish.yaml). It must match the tunnel's
  public hostname, or login redirects break. Change both together.
- **`latest` tag.** Argo CD won't notice a new image. Pin a version tag to upgrade
  deliberately.
- **Single replicas.** Postgres, Valkey and Norish each run one pod; a node loss means
  a short outage while Longhorn re-attaches the volume elsewhere.
- **Backups.** Nothing backs these volumes up yet (open item: Longhorn backups).
