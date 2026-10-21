# Storage with Longhorn

Continues from [ARGOCD.md](ARGOCD.md). Gives the cluster replicated persistent
volumes: a PVC gets a volume stored on two different workers, so losing one worker
or its disk does not lose the data.

## How it works

```
pod -> PVC -> Longhorn volume -> 2 replicas, on 2 different workers' data disks
```

Each worker gets a second virtual disk (64GB). Talos formats it and mounts it at
`/var/mnt/longhorn`. Longhorn stores replicas there. The ~16GB system disk is
untouched, so apps can't fill the OS.

Capacity: 3 workers x 64GB raw is about 96GB usable with 2 replicas, minus a 10%
reserve per disk.

Replication protects against a disk or VM failing. It is **not a backup**: deleting
a PVC, or a bad application write, is replicated too. Backups are a later step.

## What changed in git

| File | Purpose |
| ---- | ------- |
| `talos/talconfig.yaml` (`worker:` block) | Longhorn extensions in the installer image, the data-disk mount, and a kubelet mount |
| `apps/longhorn.yaml` | Argo CD Application (chart version pinned here) |
| `infrastructure/longhorn/values.yaml` | Longhorn settings: data path, 2 replicas, default StorageClass |

Two details in the Talos config:

- **Extensions** `iscsi-tools` and `util-linux-tools`. Talos has no package manager,
  so they are baked into an installer image built by Talos Image Factory. Only
  workers get them; the control plane keeps the stock image.
- **UserVolumeConfig** selects any non-system disk larger than 50GB. It stays
  pending until the data disk exists, so applying the config early is harmless.

## Do these in order

Pushing `apps/longhorn.yaml` before the nodes are ready makes the Longhorn pods fail.

### 1. Add a 64GB disk to each worker (Proxmox)

Add it to talos-wk-01, -02 and -03 only. Use the same controller type as the
existing disk. Check each node sees it:

```bash
export TALOSCONFIG=$PWD/talos/clusterconfig/talosconfig
talosctl get disks --nodes 10.0.40.31
```

You should see a second disk (`sdb`, about 64GB). If it appears as `vda`, the
controller type differs; the config doesn't depend on the name, so that's fine.

### 2. Regenerate and apply the Talos config to the workers

```bash
cd talos
talhelper genconfig
talhelper gencommand apply          # read the commands first
```

Apply to the workers only, one at a time, e.g.:

```bash
talhelper gencommand apply --node 10.0.40.31 | bash
```

The control plane doesn't need to change. Applying may reboot a node.

### 3. Upgrade each worker to the new image (one at a time)

The config only records the new installer image. An upgrade is what installs it.
This is an in-place upgrade: the node keeps its identity and rejoins the cluster.

```bash
talhelper gencommand upgrade --node 10.0.40.31   # read it first
talhelper gencommand upgrade --node 10.0.40.31 | bash
kubectl get nodes -w                              # wait for Ready again
```

Then repeat for .32 and .33. **Do one node at a time** so pods always have somewhere
to run. Cilium and the other apps tolerate a brief node drain.

Verify on each worker:

```bash
talosctl get extensions --nodes 10.0.40.31        # iscsi-tools, util-linux-tools
talosctl get volumestatus --nodes 10.0.40.31 | grep longhorn
```

You should see the extensions and a `u-longhorn` volume in phase `ready`.

### 4. Deploy Longhorn

Commit and push `apps/longhorn.yaml` and `infrastructure/longhorn/`. Then:

```bash
export KUBECONFIG=$PWD/talos/clusterconfig/kubeconfig
kubectl -n argocd get application longhorn
kubectl -n longhorn-system get pods -w
```

Give it a few minutes to pull images. Expect `longhorn-manager` and `longhorn-csi`
pods on each worker.

### 5. Open the Longhorn UI

```bash
kubectl -n longhorn-system port-forward svc/longhorn-frontend 8081:80
```

Open http://localhost:8081. Under **Node**, each worker should show a disk of
about 64GB, schedulable. (This UI has no login. Don't expose it outside the LAN
without Cloudflare Access in front.)

### 6. Test with a PVC

```bash
kubectl apply -f - <<'EOF'
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: lh-test
spec:
  accessModes: [ReadWriteOnce]
  resources:
    requests:
      storage: 1Gi
---
apiVersion: v1
kind: Pod
metadata:
  name: lh-test
spec:
  containers:
    - name: c
      image: busybox
      command: ["sh", "-c", "echo hello > /data/f && cat /data/f && sleep 3600"]
      volumeMounts:
        - { name: d, mountPath: /data }
  volumes:
    - name: d
      persistentVolumeClaim:
        claimName: lh-test
EOF
kubectl get pvc lh-test            # Bound
kubectl logs lh-test               # hello
kubectl delete pod/lh-test pvc/lh-test
```

In the UI the volume should show 2 replicas on 2 different nodes.

## Using it

Any PVC without a `storageClassName` gets Longhorn, because it is the default
StorageClass. Request `ReadWriteOnce` volumes. For `ReadWriteMany`, Longhorn
starts a share-manager NFS pod behind the scenes; that works but is slower.

## Troubleshooting

- **App stuck `OutOfSync`/`Missing`, error `longhorn-pre-upgrade ... serviceaccount
  "longhorn-service-account" not found`:** the chart's pre-upgrade check runs as an
  Argo PreSync hook before the ServiceAccount exists. It is disabled in
  `values.yaml` (`preUpgradeChecker.jobEnabled: false`). If you see it, push that
  fix, then clear the stuck attempt:
  `kubectl -n longhorn-system delete job longhorn-pre-upgrade` and
  `kubectl -n argocd patch application longhorn --type merge -p '{"operation":null}'`,
  then refresh the app.

- **Longhorn pods `CreateContainerConfigError` or privilege errors:** the namespace
  is missing the `pod-security.kubernetes.io/enforce: privileged` label. Argo sets
  it; check `kubectl get ns longhorn-system --show-labels`.
- **`longhorn-manager` crash looping, iscsi errors:** the node doesn't have the
  extensions. Run `talosctl get extensions` on it; if empty, do step 3 for that node.
- **Disk not listed in the Longhorn UI:** check `/var/mnt/longhorn` exists
  (`talosctl get volumestatus | grep longhorn`) and the data disk is larger than 50GB.
- **Volume mounts fail with "mount propagation" errors:** the kubelet extraMount is
  missing. It needs `bind, rshared, rw`. Re-apply the config and reboot the node.
- **Never `talosctl reset` a worker casually.** It wipes the data disk and one of
  your replicas. Drain it and let Longhorn rebuild elsewhere first.

## Upgrading

- **Longhorn:** bump `targetRevision` in `apps/longhorn.yaml`. Read the upgrade
  notes first; Longhorn upgrades one minor version at a time.
- **Talos:** upgrade workers one at a time and wait for volumes to show healthy in
  the Longhorn UI between nodes, so you never take down both replicas of a volume.
