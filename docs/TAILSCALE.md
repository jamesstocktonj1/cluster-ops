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
infrastructure/cilium/values.yaml                           socketLB.hostNamespaceOnly (see step 4)
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

Keep any existing rules for your own devices. Merge, don't replace. The default
allow-all rule (`*` to `*`) already lets your own devices reach `tag:k8s`; if you
replace it with narrower rules, add one for yourself too.

`tag:k8s` must be owned by `tag:k8s-operator`. Without that the operator cannot
create proxies (see Troubleshooting).

## 2. OAuth client for the operator

**Settings > Trust credentials** (older UI: OAuth clients) > create an OAuth
client with scopes **Devices Core: write**, **Auth Keys: write** and
**Services: write**, and select the tag `tag:k8s-operator` in the client's tags
field. Copy the client ID and secret. The tag must actually be selected; a client
without it cannot create tagged proxies.

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

Two cluster settings in git make the proxy pod able to start and to forward
traffic. Both were needed on this cluster:

- **Pod Security:** Talos enforces the `baseline` profile, but the proxy pods run
  privileged. `apps/tailscale-operator.yaml` labels the `tailscale` namespace
  `pod-security.kubernetes.io/enforce: privileged` through
  `managedNamespaceMetadata`. Without it the StatefulSet never creates a pod.
  Argo only applies this label during a sync, so if the Application was already
  synced, trigger a sync.
- **Cilium socket LB:** see step 4.

## 4. Cilium: restrict socket LB to the host namespace

Cilium here replaces kube-proxy, which turns on socket-level load balancing
(`Socket LB: Enabled` in `cilium-dbg status --verbose`) even though the
ConfigMap key `bpf-lb-sock` says `false`. Socket LB translates a pod's own
`connect()` calls to ClusterIPs. The Tailscale proxy does not connect to the
Service; it forwards packets and rewrites their destination to the Service's
ClusterIP in netfilter. Cilium never translates those, so they leave the pod
unchanged and nothing answers. Symptoms: the `argocd` device is online and
`tailscale ping` works, but HTTPS times out, and the proxy pod can still reach the
ClusterIP from its own shell, which makes it look fine.

The fix, already in `infrastructure/cilium/values.yaml`, makes pods use per-packet
translation:

```yaml
socketLB:
  hostNamespaceOnly: true
```

The ConfigMap change does not reach running agents. After it syncs, restart them
(pods keep their networking while an agent restarts):

```bash
kubectl -n kube-system rollout restart ds/cilium
kubectl -n kube-system rollout status ds/cilium
```

Check with `kubectl -n kube-system exec ds/cilium -c cilium-agent -- cilium-dbg
config -a | grep HostnsOnly`, which should now say `BPFSocketLBHostnsOnly: Enabled`
(it said `Disabled` before the change). This matches [Tailscale's compatibility notes](https://tailscale.com/docs/kubernetes-operator/reference/compatibility)
for Cilium with kube-proxy replacement. Do not use `bpf.lbExternalClusterIP`; it
does not fix this and exposes ClusterIPs more widely.

## 5. Test from your own machine

Join the tailnet on your machine, then:

```bash
argocd login argocd.<tailnet>.ts.net --grpc-web --username admin
```

Argo CD serves a self-signed certificate; add `--insecure` or give it a real one.

Use `tailscale ping argocd` to test the tunnel itself. Plain `ping` of the proxy is not a
useful test (it is forwarded like any other traffic, and nothing behind it answers
ICMP), so a failed `ping` means nothing.
To test the service, use `curl -k https://argocd.<tailnet>.ts.net/`.

## 6. GitHub Actions

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

## 7. Remove the Cloudflare route

In the Cloudflare dashboard, delete the Argo CD public hostname on the tunnel
(and any Cloudflare Access app for it). The routes are not in git, so there is
nothing to change here.

## Troubleshooting

- **Operator `CreateContainerConfigError`:** `operator-oauth` is missing or its
  keys aren't `client_id` and `client_secret`.
- **Operator logs `requested tags [tag:k8s] are invalid or not permitted (400)`:**
  `tagOwners` in step 1 is missing `"tag:k8s": ["tag:k8s-operator"]`, or the OAuth
  client was created without the `tag:k8s-operator` tag selected (step 2). The
  operator retries on its own once fixed.
- **`argocd-server-tailscale` EXTERNAL-IP stays `<pending>`:** look at
  `kubectl -n tailscale logs deploy/operator` and `kubectl -n tailscale get sts`.
- **StatefulSet `ts-argocd-server-tailscale-*` stays 0/1 with no pod:**
  `kubectl -n tailscale describe sts` shows `violates PodSecurity "baseline:latest":
  privileged`. The `tailscale` namespace is missing the `privileged` label (see
  step 3). After fixing it the StatefulSet controller can sit in retry backoff for
  several minutes; deleting the StatefulSet makes the operator recreate it.
- **Device is online, `tailscale ping` works, but HTTPS times out:** this is the
  Cilium socket LB problem in step 4, not an ACL. To confirm, run a debug
  container with `nft` and `tcpdump` in the proxy pod (`kubectl -n tailscale debug
  <proxy-pod> --image=nicolaka/netshoot --target=tailscale --profile=netadmin`).
  On `tailscale0` you will see the SYN arrive, and on `eth0` the same SYN going to
  the ClusterIP with no reply. `cilium-dbg monitor --related-to <endpoint id>`
  shows it leaving as `-> stack ... world` untranslated.
- **CI can't connect:** check the `tag:ci` grant, and that the runner shows in the
  machine list while the job runs.
