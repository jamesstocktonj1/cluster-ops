# Tailscale

Continues from [ARGOCD.md](ARGOCD.md). Puts Argo CD on your tailnet so you can
reach it privately, including from GitHub Actions, instead of through the
Cloudflare tunnel.

```
GitHub runner (joins tailnet as tag:ci) -> argocd.<tailnet>.ts.net -> proxy pod -> argocd-server
```

What is in git:

```
apps/tailscale-operator.yaml, apps/tailscale-config.yaml   Argo CD Applications
infrastructure/tailscale/values.yaml                        operator Helm values
infrastructure/tailscale/config/argocd.yaml                 Service that exposes Argo CD
```

## 1. Tailnet policy file

In the Tailscale admin console (**Access controls**), add tags, and ACLs that let
CI reach only Argo CD:

```json
"tagOwners": {
  "tag:k8s-operator": [],
  "tag:k8s":          ["tag:k8s-operator"],
  "tag:ci":           ["autogroup:admin"],
},
"grants": [
  {"src": ["tag:ci"], "dst": ["tag:k8s"], "ip": ["tcp:443"]},
],
```

Keep any existing rules for your own devices. Merge, don't replace.

## 2. OAuth client for the operator

**Settings > Trust credentials** (older UI: OAuth clients) > create an OAuth
client with scopes **Devices Core: write**, **Auth Keys: write** and
**Services: write**, tag `tag:k8s-operator`. Copy the client ID and secret.

## 3. Create the Secret by hand

Like the Cloudflare token, this is intentionally not in git:

```bash
export KUBECONFIG=$PWD/talos/clusterconfig/kubeconfig
kubectl create namespace tailscale --dry-run=client -o yaml | kubectl apply -f -
read -p "Client ID: " ID; read -s -p "Client secret: " SECRET; echo
kubectl -n tailscale create secret generic operator-oauth \
  --from-literal=client_id="$ID" --from-literal=client_secret="$SECRET"
unset ID SECRET
```

Push the repo changes. Argo creates the operator (it retries until the Secret
exists), then the Argo CD Service:

```bash
kubectl -n argocd get applications
kubectl -n tailscale get pods                 # operator + a proxy pod for argocd
kubectl -n argocd get svc argocd-server-tailscale   # EXTERNAL-IP becomes the tailnet IP
```

`argocd` and `talos-operator` appear in the Tailscale machine list.

## 4. Test from your own machine

Join the tailnet on your machine and note that your device needs access to
`tag:k8s` in the policy (add a grant for yourself if you use tag-restricted
ACLs). Then:

```bash
argocd login argocd.<tailnet>.ts.net --grpc-web --username admin
```

Argo CD serves a self-signed certificate; add `--insecure` or give it a real one.

## 5. GitHub Actions

Create a second OAuth client for CI: scope **Auth Keys: write**, tag `tag:ci`.
Store its values as repo secrets `TS_OAUTH_CLIENT_ID` and `TS_OAUTH_SECRET`, plus
an Argo CD API token as `ARGOCD_AUTH_TOKEN`.

Create the token for a local `ci` account (Argo CD edits `argocd-cm` and
`argocd-rbac-cm`; do this once in `bootstrap/argocd-values.yaml` under `configs.cm`
and `configs.rbac`, e.g. `accounts.ci: apiKey` and `p, role:ci, applications, sync,
*/*, allow`), then:

```bash
argocd account generate-token --account ci
```

Workflow steps:

```yaml
- name: Connect to tailnet
  uses: tailscale/github-action@v3
  with:
    oauth-client-id: ${{ secrets.TS_OAUTH_CLIENT_ID }}
    oauth-secret: ${{ secrets.TS_OAUTH_SECRET }}
    tags: tag:ci
- name: Install Argo CD CLI
  run: |
    curl -sSL -o argocd https://github.com/argoproj/argo-cd/releases/download/v3.1.0/argocd-linux-amd64
    chmod +x argocd && sudo mv argocd /usr/local/bin/
- name: Sync
  env:
    ARGOCD_SERVER: argocd.<tailnet>.ts.net
    ARGOCD_AUTH_TOKEN: ${{ secrets.ARGOCD_AUTH_TOKEN }}
  run: argocd app sync root --grpc-web --insecure
```

Pin the CLI version to your server's (`kubectl -n argocd get deploy argocd-server
-o jsonpath='{..image}'`).

## 6. Remove the Cloudflare route

In the Cloudflare dashboard, delete the Argo CD public hostname on the tunnel
(and any Cloudflare Access app for it). The routes are not in git, so there is
nothing to change here.

## Troubleshooting

- **Operator `CreateContainerConfigError`:** `operator-oauth` is missing or its
  keys aren't `client_id` and `client_secret`.
- **Operator logs `requested tags are invalid or not permitted`:** `tagOwners` in
  step 1 is missing or the OAuth client doesn't own `tag:k8s-operator`.
- **`argocd-server-tailscale` EXTERNAL-IP stays `<pending>`:** look at
  `kubectl -n tailscale logs deploy/operator` and `kubectl -n tailscale get sts`.
- **CI can't connect:** check the `tag:ci` grant, and that the runner shows in the
  machine list while the job runs.
