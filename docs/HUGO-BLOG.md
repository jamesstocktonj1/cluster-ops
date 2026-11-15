# Hugo blog

Continues from [CLOUDFLARE.md](CLOUDFLARE.md) (the tunnel). Serves the static blog
built from the [hugo-blog](https://github.com/jamesstocktonj1/hugo-blog) repo, published
as `ghcr.io/jamesstocktonj1/hugo-blog:latest`. Public through the Cloudflare Tunnel at
`https://blog.jamesstockton.com`, with no LAN address.

```
browser -> Cloudflare -> tunnel -> cloudflared -> hugo-blog (ClusterIP :80) -> 2 pods
```

The manifests are **not in this repo**: they live in the hugo-blog repo's
[deploy/](https://github.com/jamesstocktonj1/hugo-blog/tree/main/deploy) folder.
[apps/hugo-blog.yaml](../apps/hugo-blog.yaml) is the only part here; it points Argo CD
at that folder. No volumes and no Secrets.

## Releasing

Push to `main` in hugo-blog. The `Release` workflow builds and pushes the image, then its
`deploy` job commits the new `sha-...` tag to `deploy/hugo-blog.yaml`. Argo CD syncs that
commit and rolls the pods. To roll back, revert the `deploy:` commit.

## Deploy

1. Merge to `main`; Argo CD creates the `hugo-blog` app. Check it:

   ```sh
   kubectl -n hugo-blog get pods,svc
   ```

2. Add the public hostname to the `stack` tunnel in the Cloudflare dashboard:

   | Field     | Value                                        |
   |-----------|----------------------------------------------|
   | Subdomain | `blog`                                       |
   | Domain    | `jamesstockton.com`                          |
   | Type      | HTTP                                         |
   | URL       | `hugo-blog.hugo-blog.svc.cluster.local:80`   |

   Don't put this one behind Cloudflare Access: it's a public site.

## Things to know

- **Container port.** The image is nginx-unprivileged and listens on 8080; the
  Service maps port 80 to it.
- **Private repo.** If the hugo-blog repo is private, Argo CD needs a read credential
  for it. Public needs nothing.
- **`baseURL`.** The Dockerfile builds with `--baseURL blog.jamesstockton.com`; it
  probably wants the scheme (`https://blog.jamesstockton.com/`), or links and the
  sitemap come out wrong.
