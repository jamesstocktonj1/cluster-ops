# Topology: two Proxmox hosts

The five VMs run on two Proxmox hosts:

| Proxmox host | VMs |
| ------------ | --- |
| `stack`      | talos-cp-01, talos-wk-01, talos-wk-04 |
| `stacklite`  | talos-wk-02, talos-wk-03 |

Traffic between VMs on the same host stays on its virtual bridge (10G+). Traffic
between hosts crosses the physical 1G link. The goal is to keep as much traffic as
possible on one host, without losing the ability to survive a whole host going down.

## How it works

Each node carries its Proxmox host as the standard zone label,
`topology.kubernetes.io/zone`, set in [talos/talconfig.yaml](../talos/talconfig.yaml).
Everything below reads that label.

| What | Where | Effect |
| ---- | ----- | ------ |
| Longhorn data locality `best-effort` | `infrastructure/longhorn/values.yaml` | One replica on the pod's node: reads are local, each write goes to one other node |
| Longhorn zone anti-affinity + auto-balance | same | The two replicas go on different hosts, so a volume survives losing a host. Older volumes are moved to match |
| `longhorn-stack` / `longhorn-stacklite` StorageClasses | `infrastructure/longhorn/config/` | Both replicas on one host. Writes stay off the 1G link, but the volume does not survive losing that host |
| Prometheus on `stacklite` | `infrastructure/monitoring/values.yaml` | The busiest writer: its pod and both replicas stay on one host |
| Grafana prefers Prometheus's host | same | Dashboard queries stay on one host |
| Cilium `loadBalancer.serviceTopology` | `infrastructure/cilium/values.yaml` | Services with `trafficDistribution: PreferSameZone` use backends on the caller's host first |
| Envoy Service `PreferSameZone` + one Envoy per host | `infrastructure/envoy-gateway/config/gateway.yaml` | Tailscale proxy -> Envoy stays on one host |
| One replica per host | Pi-hole, cloudflared, Envoy | They stay up if a host goes down |
| 4 wasmCloud hosts | `infrastructure/wasmcloud/values.yaml` | One per worker, two per Proxmox host |

The rules that spread pods over hosts are preferences (`ScheduleAnyway`), so a pod can
still start while a host is down. Pi-hole's existing one-per-node rule stays hard.

### Limits

- **Envoy -> wasmCloud is not same-host aware.** The wasmCloud operator writes its own
  EndpointSlices without node or zone information, so neither Envoy nor Cilium can
  tell which host an endpoint is on. Envoy picks any wasmCloud host.
- **Longhorn writes to a volume spread over both hosts still cross the link once.**
  That is the price of a copy on the other host. Only the per-host classes avoid it.
- **CoreDNS** is deployed by Talos, so its Service is not managed here.

## Rolling it out

Do these in order. Label the nodes **before** merging to `main`: until the labels
exist, Prometheus has no node it is allowed on and stops, and `longhorn-stacklite`
volumes have no node to put replicas on.

### 1. Label the nodes (Talos)

```bash
cd talos
talhelper genconfig
talhelper gencommand apply          # read the commands first
```

Apply to each node, one at a time. Labels and annotations apply without a reboot.

```bash
talhelper gencommand apply --node 10.0.40.30 | bash
```

Check:

```bash
kubectl get nodes -L topology.kubernetes.io/zone
```

Longhorn picks up the node tags within a minute: under **Node** in the UI, each worker
shows `stack` or `stacklite`. The annotation only applies to a node that has no
tags yet. If one already has tags, set them in the UI.

### 2. Merge and sync

Merge to `main`. Argo CD applies the rest. Then restart Cilium so it reads the new setting:

```bash
kubectl -n kube-system rollout restart ds/cilium
kubectl -n kube-system rollout status ds/cilium
```

`replicaAutoBalance` then moves replicas of existing volumes that had both copies on one
host. That is a one-off rebuild over the 1G link. Watch it in the Longhorn UI.

Existing volumes keep the data locality they were created with. To change one, open it
in the Longhorn UI and use **Update Data Locality** -> `best-effort`.

### 3. Move Prometheus onto `longhorn-stacklite`

A StatefulSet cannot change the StorageClass of an existing claim, so the old PVC
must be deleted. **This throws away the stored metrics** (up to 15 days).

```bash
kubectl -n monitoring get pvc
# Pick the prometheus-...-db-prometheus-...-0 claim, then:
kubectl -n monitoring delete pvc <that-pvc> --wait=false
kubectl -n monitoring delete pod -l app.kubernetes.io/name=prometheus
```

The new pod gets a fresh claim on `longhorn-stacklite`, on wk-02 or wk-03. Check:

```bash
kubectl -n monitoring get pvc            # STORAGECLASS longhorn-stacklite
kubectl -n monitoring get pod -l app.kubernetes.io/name=prometheus -o wide
```

Grafana moves next to it the next time its pod is rescheduled. Run
`kubectl -n monitoring delete pod -l app.kubernetes.io/name=grafana` to do it now.

### 4. Check the spread

```bash
kubectl get pods -A -o wide | grep -E 'pihole|cloudflared|envoy-envoy|hostgroup'
```

Each of Pi-hole, cloudflared and Envoy should have pods on both hosts. Pods placed
before the labels existed only move when they are recreated. To move one now:
`kubectl rollout restart` its Deployment.

## Moving a VM to the other host

Change `topology.kubernetes.io/zone` and the Longhorn annotation for that node in
`talos/talconfig.yaml` and apply. Then change its tag in the Longhorn UI, because
Longhorn does not re-read the annotation once a node has tags. Replicas on the per-host
classes now sit on the wrong host. Longhorn moves them only as they are rebuilt.

## Measuring

The Proxmox graphs for each host's physical NIC show the traffic between hosts. Compare a
quiet hour before and after.
