# Hugo blog

Continues from [CLOUDFLARE.md](CLOUDFLARE.md) (the tunnel). Serves the static blog
built from the [hugo-blog](https://github.com/jamesstocktonj1/hugo-blog) repo, published
as `ghcr.io/jamesstocktonj1/hugo-blog:latest`. Public through the Cloudflare Tunnel at
`https://blog.jamesstockton.com`, with no LAN address.

```
browser -> Cloudflare -> tunnel -> cloudflared -> hugo-blog (ClusterIP :80) -> 2 pods
```

Manifests are in [infrastructure/hugo-blog/](../infrastructure/hugo-blog/), deployed by
[apps/hugo-blog.yaml](../apps/hugo-blog.yaml). No volumes and no Secrets.

## Deploy

1. Commit and push; Argo CD creates the `hugo-blog` app. Check it:

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

- **Container port.** The Deployment assumes the image listens on port 80 (nginx).
  If it doesn't, change `containerPort` in
  [hugo-blog.yaml](../infrastructure/hugo-blog/hugo-blog.yaml).
- **Private image.** If the ghcr package is private, the pods need an
  `imagePullSecret`. Making the package public is simpler for a public blog.
- **`latest` tag.** Argo CD won't notice a new image. After publishing a new build:
  `kubectl -n hugo-blog rollout restart deploy/hugo-blog`.
- **`baseURL`.** `hugo.toml` in the blog repo must be set to the public URL, or
  links and the sitemap point at the wrong host.
