# Kargo

Continues from [ARGOCD.md](ARGOCD.md) and [TAILSCALE.md](TAILSCALE.md). Kargo
promotes a version (an image tag, a chart version, a git commit) through your
environments, for example `dev -> staging -> prod`. Each promotion commits the new
version to git and tells Argo CD to sync it. Argo CD still does all the deploying.

```
Warehouse (watches image/git/chart) -> Freight -> Stage dev -> Stage staging -> Stage prod
                                                      |             |              |
                                              commit to git + sync the Argo CD Application
```

What is in git:

```
apps/cert-manager.yaml           cert-manager (Kargo's webhooks need certificates)
apps/kargo.yaml                  Kargo Helm chart (version pinned here)
infrastructure/kargo/values.yaml Kargo Helm values
```

Kargo itself is only the engine. The environments (a `Project`, `Warehouse` and
`Stage`s) are not set up yet; see "Next steps".

## 1. Create the admin Secret by hand

Like the Tailscale and Cloudflare secrets, this is intentionally not in git (the
repo is public). It holds the admin password hash and the key that signs admin
tokens. Needs the [Kargo CLI](https://docs.kargo.io/user-guide/cli/installation) for
`kargo hash-password`:

```bash
export KUBECONFIG=$PWD/talos/clusterconfig/kubeconfig
pass=$(openssl rand -base64 48 | tr -d "=+/" | head -c 32)
echo "Admin password (save it): $pass"
kubectl create namespace kargo --dry-run=client -o yaml | kubectl apply -f -
kubectl -n kargo create secret generic kargo-admin \
  --from-literal=ADMIN_ACCOUNT_PASSWORD_HASH="$(kargo hash-password --password "$pass")" \
  --from-literal=ADMIN_ACCOUNT_TOKEN_SIGNING_KEY="$(openssl rand -base64 48 | tr -d "=+/" | head -c 32)"
```

## 2. Deploy

Merge to `main`. Argo CD creates `cert-manager` (wave 0), then `kargo` (wave 1).

```bash
kubectl -n cert-manager get pods
kubectl -n kargo get pods
```

## 3. Log in

The UI is on the tailnet at `http://kargo.<tailnet>.ts.net` (user `admin`, the
password from step 1). Or from the CLI:

```bash
kargo login http://kargo.<tailnet>.ts.net --admin
```

Without Tailscale, `kubectl -n kargo port-forward svc/kargo-api 8080:80`.

## Next steps: your first pipeline

1. **A Kargo `Project`** (one namespace per project) with a `Warehouse` that
   watches your image or chart, and one `Stage` per environment.
2. **One Argo CD Application per environment**, each annotated so Kargo may update
   it:

   ```yaml
   annotations:
     kargo.akuity.io/authorized-stage: "<project>:<stage>"
   ```

3. **Git write access.** `git-push` steps need a credential Secret in the Kargo
   project (a GitHub token with write access to the repo Kargo commits to). Don't
   commit it; create it by hand or through the Kargo UI.
4. Keep per-environment config in the repo (Kustomize overlays or a values file per
   environment) so each Stage has something to commit to.

## The kargo-demo environments

[apps/kargo-demo.yaml](../apps/kargo-demo.yaml) is an ApplicationSet that creates
`kargo-demo-dev`, `kargo-demo-staging` and `kargo-demo-prod` from the Helm chart in the
[kargo-demo](https://github.com/jamesstocktonj1/kargo-demo) repo (`charts/kargo-demo`,
with `deploy/<env>/values.yaml` per environment). They deploy to the `kargo-demo-<env>` namespaces and are annotated for the Kargo stages
`kargo-demo:<env>`. The pipeline is `dev -> staging -> prod`: dev and staging promote automatically, prod
is promoted by hand and only after the freight has sat in staging for 5 minutes. Auto-sync is off on purpose: Kargo syncs after it commits the new
image tag. The first deploy of each is a manual sync.

Public hostnames, added to the `stack` tunnel in the Cloudflare dashboard (routes are
not in git, see [CLOUDFLARE.md](CLOUDFLARE.md)). No Cloudflare Access; the app has no
secrets:

| Subdomain         | Domain              | Type | URL                                          |
|-------------------|---------------------|------|----------------------------------------------|
| `kargo-demo-dev`  | `jamesstockton.com` | HTTP | `kargo-demo.kargo-demo-dev.svc.cluster.local:80`  |
| `kargo-demo-staging` | `jamesstockton.com` | HTTP | `kargo-demo.kargo-demo-staging.svc.cluster.local:80` |
| `kargo-demo-prod` | `jamesstockton.com` | HTTP | `kargo-demo.kargo-demo-prod.svc.cluster.local:80` |

```bash
curl -X POST https://kargo-demo-dev.jamesstockton.com/greet \
  -H 'Content-Type: application/json' -d '{"name":"James"}'
```

## Things to know

- **No prune on the Application**, so a bad commit can't delete Kargo's CRDs and
  every Project and Stage with them. Same for cert-manager.
- **Argo Rollouts integration is off** because Rollouts isn't installed. Flip
  `controller.rollouts.integrationEnabled` if you add it.
- **Tailscale tag.** The Service uses `tag:k8s`, so the existing tailnet policy
  already lets the operator create the proxy.
- **Upgrades.** Bump `targetRevision` in `apps/kargo.yaml` (or `apps/cert-manager.yaml`)
  and read the release notes first; Kargo's CRDs are applied by the chart.
