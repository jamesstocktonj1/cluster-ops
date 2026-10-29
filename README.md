# Cluster OPs - Talos HomeLab

The configuration of my self-hosted Talos Linux Kubernetes cluster. Everything is
GitOps: push to `main` and Argo CD applies it.

```
┌──────────────────────────────────────────────────────────────────────────┐
│  ACCESS     Cloudflare Tunnel (public)          Tailscale (private)      │
├──────────────────────────────────────────────────────────────────────────┤
│  WORKLOADS  wasmCloud (WebAssembly)       Envoy Gateway (routing)        │
├──────────────────────────────────────────────────────────────────────────┤
│  GITOPS     Argo CD  <──  this repo                                      │
├──────────────────────────────────────────────────────────────────────────┤
│  PLATFORM   Cilium (network, LB)          Longhorn (storage)             │
├──────────────────────────────────────────────────────────────────────────┤
│  KUBERNETES v1.35 - 1 control plane + 3 workers                          │
├──────────────────────────────────────────────────────────────────────────┤
│  OS         Talos Linux - immutable, API-managed, no SSH                 │
├──────────────────────────────────────────────────────────────────────────┤
│  HARDWARE   Proxmox - 4 VMs                                              │
└──────────────────────────────────────────────────────────────────────────┘
```

## What's running

- **Talos Linux**: the only thing on the VMs. Configured from
  [talos/talconfig.yaml](talos/talconfig.yaml).
- **Cilium**: CNI that replaces kube-proxy, and hands out LAN IPs to
  `LoadBalancer` Services.
- **Argo CD**: watches [apps/](apps/) and deploys everything else.
- **Longhorn**: replicated persistent volumes across the workers.
- **Envoy Gateway**: Gateway API ingress with path-based routing.
- **Cloudflare Tunnel**: public access with no open ports on the router.
- **Tailscale operator**: private access to the cluster, including Argo CD from
  GitHub Actions.
- **wasmCloud v2**: runs WebAssembly components.
- **Prometheus Operator**: metrics, scraping opted-in pods.

More detail for each is in [docs/](docs/).
