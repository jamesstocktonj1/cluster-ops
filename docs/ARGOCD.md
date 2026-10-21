# Argo CD and GitOps

Continues from [BOOTSTRAP.md](BOOTSTRAP.md). After this, pushing to `main`
changes the cluster. Run commands from the repo root with
`export KUBECONFIG=$PWD/talos/clusterconfig/kubeconfig`.

## How it fits together

```
bootstrap/
  argocd-values.yaml   Helm values for the one-time Argo CD install
  root-app.yaml        the "root" Application, applied by hand once
apps/                  one Application file per thing Argo deploys
  cilium.yaml            Cilium Helm chart (version pinned here)
  cilium-config.yaml     LoadBalancer IP pool + L2 policy
infrastructure/
  cilium/
    values.yaml          Cilium Helm values
    config/              plain manifests (IP pool, L2 policy)
```

The chain: you install Argo CD by hand once, then apply `root-app.yaml`. The root
Application watches `apps/` and creates every Application it finds. Each of those
deploys a chart or a folder of manifests. To deploy something new, add a file to
`apps/` and push.

Applications use sync waves: `cilium` (wave 0) before `cilium-config` (wave 1).

## 1. Push the repo first

Argo CD reads from GitHub, not your disk. Commit and push `main` before step 3 or
the root app will find nothing. The repo is public, so Argo needs no credentials.

## 2. Install Argo CD

```bash
helm repo add argo https://argoproj.github.io/argo-helm
helm repo update

helm install argocd argo/argo-cd \
  --version 10.10.5 \
  --namespace argocd --create-namespace \
  -f bootstrap/argocd-values.yaml

kubectl -n argocd rollout status deploy/argocd-server
```

## 3. Hand the cluster to Argo

```bash
kubectl apply -f bootstrap/root-app.yaml
kubectl -n argocd get applications
```

Within a minute you should see `root`, `cilium` and `cilium-config`.

### Cilium adoption

Cilium is already running from your manual `helm install`. Argo renders the same
chart (1.20.3) and applies it over the top, so the first sync should show only
small differences: the new `l2announcements` and `k8sClientRateLimit` settings,
which restart the Cilium pods one node at a time. Pods keep their networking
during that.

Check that `cilium` ends up **Synced** and **Healthy**. If it shows an unexpected
diff, run `kubectl -n argocd get application cilium -o yaml` and look under
`status.resources` before forcing a sync.

Once it is healthy, delete Helm's leftover record of the manual install. This
removes only Helm's bookkeeping Secret, not Cilium itself:

```bash
kubectl -n kube-system delete secret -l owner=helm,name=cilium
```

From now on, change Cilium by editing `infrastructure/cilium/values.yaml` and
pushing. Never run `helm upgrade cilium` by hand again.

## 4. Open the Argo CD UI

```bash
kubectl -n argocd port-forward svc/argocd-server 8080:443
```

Open https://localhost:8080 (accept the self-signed certificate). User `admin`;
the initial password is:

```bash
kubectl -n argocd get secret argocd-initial-admin-secret \
  -o jsonpath='{.data.password}' | base64 -d; echo
```

Change it in the UI, then delete that secret.

## 5. Test the LoadBalancer pool

A Service of type LoadBalancer should get an IP from 10.0.40.200-220:

```bash
kubectl create deployment lbtest --image=nginx
kubectl expose deployment lbtest --type=LoadBalancer --port=80
kubectl get svc lbtest        # EXTERNAL-IP should be in the pool
curl http://<EXTERNAL-IP>     # from another machine on the LAN
kubectl delete svc,deployment lbtest
```

If `EXTERNAL-IP` stays `<pending>`, check
`kubectl get ciliumloadbalancerippool` and that no DHCP lease overlaps the range.
If it has an IP but the LAN can't reach it, check
`kubectl get ciliuml2announcementpolicy` and the node NIC name (`ens18`).

## Day to day

- **Change something:** edit the file, push, and Argo applies it (about 3 minutes,
  or press Refresh in the UI).
- **Upgrade Cilium:** bump `targetRevision` in `apps/cilium.yaml`, push. Read the
  Cilium upgrade notes first.
- **Remove an app:** delete its file from `apps/` and push. The root app prunes the
  Application, but only an app with the `resources-finalizer.argocd.argoproj.io`
  finalizer takes its pods, Services and namespace with it. Without the finalizer
  they are left running, orphaned. Give every new app file the finalizer (copy
  `apps/cloudflared.yaml`). `cilium` omits it on purpose, so removing it can never
  delete the CNI.
- **Don't `kubectl edit` managed resources.** `selfHeal` reverts it.

## Not covered yet

- Secrets in Argo (decrypting SOPS files)
- Argo CD managing its own upgrades
- An Ingress or LoadBalancer IP for the Argo CD UI
