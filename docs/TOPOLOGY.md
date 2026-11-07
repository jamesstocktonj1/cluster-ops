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
| Grafana prefers Prometheus's host | `infrastructure/monitoring/values.yaml` | Dashboard queries stay on one host |
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
- **Longhorn is not tuned for this yet.** Its replication is still the largest source
  of traffic between hosts. Longhorn reads the zone label on its own, though: with its
  default settings, new replicas prefer different hosts. Existing replicas do not move.
- **CoreDNS** is deployed by Talos, so its Service is not managed here.

## Rolling it out

Do these in order: label the nodes before merging to `main`, or the spread rules
have nothing to act on.

### 1. Label the nodes (Talos)

```bash
cd talos
talhelper genconfig
talhelper gencommand apply          # read the commands first
```

Apply to each node, one at a time. Labels apply without a reboot.

```bash
talhelper gencommand apply --node 10.0.40.30 | bash
```

Check:

```bash
kubectl get nodes -L topology.kubernetes.io/zone
```

### 2. Merge and sync

Merge to `main`. Argo CD applies the rest. Then restart Cilium so it reads the new setting:

```bash
kubectl -n kube-system rollout restart ds/cilium
kubectl -n kube-system rollout status ds/cilium
```

### 3. Check the spread

```bash
kubectl get pods -A -o wide | grep -E 'pihole|cloudflared|envoy-envoy|hostgroup'
```

Each of Pi-hole, cloudflared and Envoy should have pods on both hosts. Pods placed
before the labels existed only move when they are recreated. To move one now:
`kubectl rollout restart` its Deployment. Grafana moves next to Prometheus the next
time its pod is rescheduled.

## Moving a VM to the other host

Change `topology.kubernetes.io/zone` for that node in `talos/talconfig.yaml` and apply.

## Measuring

The Proxmox graphs for each host's physical NIC show the traffic between hosts. Compare a
quiet hour before and after.
