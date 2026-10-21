# Bootstrapping the cluster

How to take four blank Talos VMs to a working Kubernetes cluster with Cilium.
Run every command from the repo root unless a step says otherwise.
The cluster is 4 nodes: 1 control plane and 3 workers.

**Scope:** this covers Talos + Cilium. Next is [ARGOCD.md](ARGOCD.md). Cloudflare,
storage and workloads are not written yet (see [Not covered yet](#not-covered-yet)).

| Node      | IP          | Role          |
| --------- | ----------- | ------------- |
| talos-cp-01 | 10.0.40.30 | control plane |
| talos-wk-01 | 10.0.40.31 | worker        |
| talos-wk-02 | 10.0.40.32 | worker        |
| talos-wk-03 | 10.0.40.33 | worker        |

The source of truth is [talos/talconfig.yaml](../talos/talconfig.yaml).

---

## 0. Before you start

- **Tools installed:** `talosctl`, `talhelper`, `sops`, `age`, `kubectl`, `helm`.
- **age private key backed up.** It lives at `~/.config/sops/age/keys.txt`.
  Without it you cannot decrypt `talos/talsecret.sops.yaml`. Store a copy in your
  password manager. Never commit it.
- **DHCP reservations** on the router give each VM its IP above. New VMs have new
  MACs, so update the reservations first. If an IP differs, edit `talconfig.yaml`.
- **Talos ISO v1.12.1** (matching `talosVersion` in `talconfig.yaml`) is attached
  to each VM, with `/dev/sda` as the target disk.

## 1. Boot the VMs into maintenance mode

Start all four VMs from the ISO. Each one boots into *maintenance mode*: Talos is
running from RAM, has no config, and waits for one. The console shows its IP.

Check each node is reachable and sees its disk. `--insecure` is needed because the
node has no certificates yet.

```bash
talosctl get disks --insecure --nodes 10.0.40.30
# repeat for .31 .32 .33
```

You should see `sda`. If a node is unreachable, check its DHCP reservation.

## 2. Generate the machine configs

```bash
cd talos
talhelper genconfig
```

This decrypts `talsecret.sops.yaml` (using your age key) and writes one config per
node plus a `talosconfig` into `talos/clusterconfig/`. That directory is
gitignored because it contains secrets.

Point `talosctl` at the generated client config for the rest of the session:

```bash
export TALOSCONFIG=$PWD/clusterconfig/talosconfig
```

## 3. Apply the configs

`talhelper` prints the right `apply-config` command for each node. Read them first:

```bash
talhelper gencommand apply --extra-flags=--insecure
```

If they look right, run them:

```bash
talhelper gencommand apply --extra-flags=--insecure | bash
```

`--insecure` is only for this first apply, while nodes are in maintenance mode.
Each node installs Talos to `/dev/sda` and reboots. Eject the ISO or set the boot
disk first so they don't boot the ISO again. Wait a few minutes.

## 4. Bootstrap etcd (once, on the control plane)

```bash
talhelper gencommand bootstrap | bash
```

This runs `talosctl bootstrap` against talos-cp-01 (10.0.40.30). **Run it exactly once,
on one node, ever.** Running it again on a live cluster can split etcd.

Watch it come up:

```bash
talosctl get members --nodes 10.0.40.30
```

All four nodes should be listed. Don't run `talosctl health` yet: it waits for
Ready nodes and fails until Cilium is installed (step 6). Run it in step 7.

## 5. Get a kubeconfig

Write it to a file in the repo (gitignored) rather than your global config:

```bash
talosctl kubeconfig clusterconfig/kubeconfig --nodes 10.0.40.30
export KUBECONFIG=$PWD/clusterconfig/kubeconfig
kubectl get nodes
```

All four nodes should be listed as **NotReady**. That is expected: there is no CNI
yet (we disabled Flannel on purpose), so pods cannot get networking.

## 6. Install Cilium

Cilium replaces both Flannel and kube-proxy. On Talos it needs a few specific
settings: it must reach the API through Talos's local proxy (`localhost:7445`,
KubePrism), and cgroups are already mounted by Talos.

```bash
helm repo add cilium https://helm.cilium.io
helm repo update
helm search repo cilium/cilium   # pick a current stable version for VERSION below

helm install cilium cilium/cilium \
  --version 1.20.3 \
  --namespace kube-system \
  --set ipam.mode=kubernetes \
  --set kubeProxyReplacement=true \
  --set k8sServiceHost=localhost \
  --set k8sServicePort=7445 \
  --set cgroup.autoMount.enabled=false \
  --set cgroup.hostRoot=/sys/fs/cgroup \
  --set securityContext.capabilities.ciliumAgent="{CHOWN,KILL,NET_ADMIN,NET_RAW,IPC_LOCK,SYS_ADMIN,SYS_RESOURCE,DAC_OVERRIDE,FOWNER,SETGID,SETUID}" \
  --set securityContext.capabilities.cleanCiliumState="{NET_ADMIN,SYS_ADMIN,SYS_RESOURCE}"
```

Record the version you chose. Once Argo CD manages Cilium, it must use the same
version and values so it adopts this install instead of fighting it.

Wait for it:

```bash
kubectl -n kube-system rollout status ds/cilium
kubectl get nodes        # all four should become Ready
```

## 7. Verify

```bash
kubectl get nodes -o wide
kubectl get pods -A                       # nothing stuck in Pending/CrashLoop
talosctl health --nodes 10.0.40.30
kubectl -n kube-system exec ds/cilium -- cilium-dbg status --brief
```

`cilium-dbg status` should report `OK`, and there should be no `kube-proxy` pods.

## 8. Back up

Take an etcd snapshot now, and again before any upgrade. With a single control
plane, losing it loses the cluster state, so snapshots are your recovery plan.

```bash
talosctl etcd snapshot etcd-$(date +%F).db --nodes 10.0.40.30
```

Keep snapshots out of git; they contain cluster secrets.

---

## Wiping and starting over

Reset a node back to maintenance mode (destroys its state and data):

```bash
talosctl reset --nodes 10.0.40.31 --graceful=false --reboot \
  --system-labels-to-wipe STATE --system-labels-to-wipe EPHEMERAL
```

Then redo from step 3. To get entirely new cluster identity, regenerate secrets
first (this invalidates every existing kubeconfig and talosconfig):

```bash
cd talos
talhelper gensecret > talsecret.sops.yaml
sops -e -i talsecret.sops.yaml
```

## Troubleshooting

- **`talhelper genconfig` fails to decrypt:** your age key is missing or is not the
  one in `.sops.yaml`. Check `~/.config/sops/age/keys.txt`.
- **Apply hangs or "connection refused":** the node is not in maintenance mode or
  has a different IP. Check the VM console.
- **"certificate required" on a later command:** drop `--insecure` once a node has
  its config, and make sure `TALOSCONFIG` is exported.
- **Nodes stay NotReady after Cilium:** `kubectl -n kube-system logs ds/cilium`.
  Usually a wrong `k8sServicePort` or a missing capability setting.
- **talos-cp-01 down:** there is only one control plane, so the API is unavailable until
  it is back. Running workloads keep running; restore from an etcd snapshot if the
  node is lost.

## Not covered yet

- Cloudflare (DNS, tunnel)
- Storage and workloads
