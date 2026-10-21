# Cloudflare Tunnel

Continues from [ARGOCD.md](ARGOCD.md). Lets you reach services in the cluster from
the internet at your own domain, with no router port forwarding. Services stay
LAN-only unless you add a route for them.

## How it works

```
browser -> Cloudflare (TLS, DNS) <- outbound connection <- cloudflared pods -> Service in cluster
```

`cloudflared` runs as a Deployment in the cluster and dials *out* to Cloudflare.
Requests for your hostnames arrive over that connection and are forwarded to the
Service you choose. TLS ends at Cloudflare, so no cert-manager or certificates are
needed here.

This setup uses a **remotely-managed tunnel**: the hostname-to-service routes are
set in the Cloudflare dashboard, and the cluster only needs a token. The routes
are therefore not in git.

What is in git:

```
apps/cloudflared.yaml, apps/whoami.yaml     Argo CD Applications
infrastructure/cloudflared/                 namespace + cloudflared Deployment
infrastructure/whoami/                      throwaway test app
```

## Prerequisites

- A domain on Cloudflare (nameservers pointed at Cloudflare).
- A Cloudflare account with Zero Trust enabled (the free plan is enough).
- The repo changes above committed and pushed to `main` (by you).

## 1. Create the tunnel

Menu names in the Cloudflare dashboard change occasionally; look for the tunnels
page under **Zero Trust > Networks**.

1. Create a tunnel, type **Cloudflared**, name it `stack`.
2. On the "install connector" screen, choose any environment and **copy the
   token**: the long string after `--token` or `service install`. Treat it like a
   password. Don't paste it into git, chat or a doc.
3. Don't run any install command it shows. The cluster will do that.

## 2. Let Argo create the namespace

After you push, Argo creates the `cloudflared` namespace and the Deployment:

```bash
export KUBECONFIG=$PWD/talos/clusterconfig/kubeconfig
kubectl -n argocd get applications
kubectl -n cloudflared get pods
```

The pods will show `CreateContainerConfigError` until step 3. That is expected:
they are waiting for the token Secret.

## 3. Create the token Secret by hand

This Secret is intentionally not in git. `read -s` keeps the token out of your
shell history and screen:

```bash
read -s -p "Tunnel token: " TOKEN; echo
kubectl -n cloudflared create secret generic cloudflared-token \
  --from-literal=token="$TOKEN"
unset TOKEN
```

The pods retry on their own. Within a minute:

```bash
kubectl -n cloudflared get pods     # 2/2 Running, Ready
```

In the dashboard the tunnel should now show **Healthy** with 2 connectors.

Argo CD ignores this Secret because it isn't in git, so `prune` won't delete it.
Once secrets are handled (SOPS), this step gets replaced by an encrypted file.

## 4. Route a hostname to the test app

In the tunnel's **Public Hostname** settings, add:

| Field     | Value                                      |
| --------- | ------------------------------------------ |
| Subdomain | `whoami`                                   |
| Domain    | your domain                                |
| Type      | `HTTP`                                     |
| URL       | `whoami.whoami.svc.cluster.local:80`       |

Cloudflare creates the DNS record for you.

## 5. Test

```bash
curl https://whoami.<your-domain>
```

You should get a block of text starting with `Hostname:` and including headers
such as `Cf-Connecting-Ip`. That proves: DNS -> Cloudflare -> tunnel ->
cloudflared -> Service -> pod.

## 6. Clean up the test

Delete `apps/whoami.yaml` and push; Argo prunes the app. Then remove the `whoami`
public hostname in the dashboard.

## Exposing a real service later

1. Make sure it has a `ClusterIP` Service.
2. Add a public hostname in the dashboard pointing to
   `<service>.<namespace>.svc.cluster.local:<port>`.

Anything that is an admin login (Argo CD, Omada) should not be public without
**Cloudflare Access** in front of it (Zero Trust > Access > Applications), which
adds a login via email or SSO before requests reach the cluster. Omada should stay
LAN-only; it needs a LAN LoadBalancer IP (Cilium pool, 10.0.40.200-220), not the
tunnel.

## Troubleshooting

- **Pods stuck in `CreateContainerConfigError`:** the `cloudflared-token` Secret is
  missing, or its key isn't `token`. Check with
  `kubectl -n cloudflared get secret cloudflared-token`.
- **Pods `CrashLoopBackOff`:** `kubectl -n cloudflared logs deploy/cloudflared`.
  An authentication error means the token is wrong or the tunnel was deleted.
- **Tunnel Healthy but the site returns 502/1033:** the route's URL is wrong, or
  the target Service has no ready pods. Check
  `kubectl -n whoami get svc,pods` and use the in-cluster DNS name.
- **`NXDOMAIN`:** the DNS record wasn't created, or the domain isn't on
  Cloudflare yet.
- **Rotate the token:** in the dashboard refresh the connector token, then
  `kubectl -n cloudflared delete secret cloudflared-token`, redo step 3, and
  `kubectl -n cloudflared rollout restart deploy/cloudflared`.
