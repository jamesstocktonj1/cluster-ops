# Pi-hole

Continues from [ARGOCD.md](ARGOCD.md) (Cilium hands out the LAN IP). Runs three
Pi-hole replicas behind one DNS address, `10.0.40.203`.

## How it works

```
LAN device -> 10.0.40.203:53 (Cilium LB, UDP+TCP) -> any of 3 Pi-hole pods (one per worker)
                                                          |
                                           blocked? answer 0.0.0.0 : forward to 1.1.1.1
```

- [apps/pihole.yaml](../apps/pihole.yaml): the `pihole` chart from
  <https://mojo2600.github.io/pihole-kubernetes/>, version pinned.
- [infrastructure/pihole/values.yaml](../infrastructure/pihole/values.yaml): all
  settings, commented.
- One Deployment with 3 replicas, spread across workers. One Service of type
  `LoadBalancer` with the IP pinned by `lbipam.cilium.io/ips`. One worker answers ARP
  for it and receives all queries; with `externalTrafficPolicy: Cluster`, Cilium there
  load-balances each flow across all three pods, so the replicas share the load.
- The three Pi-holes do not talk to each other. They are identical only because they
  are configured from the same file.

## Trade-offs to know about

- **No persistent volume.** The query log and anything changed in the web UI reset
  when a pod restarts. Block lists, upstream DNS and so on belong in the values
  file. This is what lets three pods run on three nodes without sharing a volume.
- **Dashboards are per replica.** A query only shows up on the replica that served
  it, and Cilium picks one per flow, so each replica sees roughly a third.
- **Client IPs are partly hidden.** Queries Cilium forwards to a pod on another node
  are SNATed, so they show the announcing node's IP as the client. Only queries
  served by the pod on the announcing node keep the real device IP. Switching back
  to `externalTrafficPolicy: Local` restores client IPs but sends all LAN DNS to one
  pod; Cilium DSR mode would give both but needs native routing.
- **The web UI shows one replica.** It is on its own LAN IP (`10.0.40.204`), but you
  only see the replica on the node announcing that IP. Use a port-forward to look at
  a specific one (below).
- **DNS is a dependency of the cluster.** Argo CD fetches Helm charts and images by
  name. Don't point the *nodes'* DNS at this IP; keep the router/Talos resolvers
  independent so a Pi-hole outage cannot stop you fixing Pi-hole.

## Deploy

### 1. Create the admin Secret by hand

Not in git (same approach as the Grafana password). The pods stay in
`CreateContainerConfigError` until it exists:

```bash
kubectl create namespace pihole   # Argo would create it, but the Secret needs it first
read -s -p "Pi-hole admin password: " PASS; echo
kubectl -n pihole create secret generic pihole-admin \
  --from-literal=password="$PASS"
unset PASS
```

### 2. Push and sync

Merge to `main`; Argo CD creates the `pihole` app. Then check:

```bash
kubectl -n pihole get pods -o wide        # 3 Running, each on a different node
kubectl -n pihole get svc                 # pihole-dns-... EXTERNAL-IP 10.0.40.203
```

### 3. Test it

```bash
dig @10.0.40.203 example.com              # resolves
dig @10.0.40.203 doubleclick.net          # 0.0.0.0 = blocked
```

Failover: `kubectl -n pihole delete pod <one pod>`; the `dig` keeps working.

### 4. Point the LAN at it

In the router's DHCP settings, set the DNS server to `10.0.40.203`. Keep a secondary
that is not Pi-hole (e.g. `1.1.1.1`) if you want browsing to survive a full outage,
at the cost of some devices bypassing the blocking.

### 5. Reach it over Tailscale (away from home)

[infrastructure/tailscale/config/pihole.yaml](../infrastructure/tailscale/config/pihole.yaml)
adds a second Service, handled by the Tailscale operator ([TAILSCALE.md](TAILSCALE.md)).
After Argo syncs, a `pihole` device appears in the Tailscale machine list:

```bash
kubectl -n pihole get svc pihole-dns-tailscale   # EXTERNAL-IP becomes the tailnet IP
dig @pihole.<tailnet>.ts.net example.com         # from any tailnet device
```

To use it automatically: Tailscale admin console > DNS > Nameservers > add the
`pihole` device's tailnet IP, and enable **Override DNS servers** if you want all
tailnet traffic to use it. Needs the ACL to allow your devices to reach `tag:k8s` on
`53` (the allow-all rule in [TAILSCALE.md](TAILSCALE.md) already does). Tailscale
queries show in Pi-hole as one client (the proxy pod).

## Web UI

On the LAN: <http://10.0.40.204/admin> (or https, accepting the certificate warning).
Log in with the password from step 1. To look at a specific replica instead:

```bash
kubectl -n pihole get pods
kubectl -n pihole port-forward pod/<pod-name> 8080:80
```

Then open <http://localhost:8080/admin>.

## Metrics

Pi-hole has no Prometheus endpoint itself, so each pod runs a second container,
`exporter` ([ekofr/pihole-exporter](https://github.com/eko/pihole-exporter)), that
reads that pod's Pi-hole API and serves it on port 9617. A `PodMonitor` made by the
chart makes Prometheus scrape it every 30s ([MONITORING.md](MONITORING.md)). Check
[http://localhost:9090/targets](http://localhost:9090/targets) (port-forward in
MONITORING.md) for `podMonitor/pihole/pihole-prometheus-exporter` with 3 targets up.

Useful series (all have a `pod` label; sum or `avg by` across replicas):

| Metric | Meaning |
| --- | --- |
| `pihole_dns_queries_today` | queries handled |
| `pihole_ads_blocked_today` | queries blocked |
| `pihole_ads_percentage_today` | percent blocked |
| `pihole_domains_being_blocked` | size of the loaded block lists |
| `pihole_unique_clients` | distinct clients seen |
| `pihole_status` | 1 = blocking enabled |
| `pihole_queries_forwarded` / `pihole_queries_cached` | forwarded upstream vs answered from cache |

Exact names vary a little by exporter version; browse them in Prometheus by typing
`pihole_`. Because pods have no volume, the "today" counters reset when a pod
restarts. For rates use `rate()` over them rather than the raw value.

Two useful alerts-to-be: `sum(pihole_status) < 3` (a replica not blocking or down) and
`up{namespace="pihole"} == 0`. There is no Alertmanager yet, so these
are only for graphs for now.

Note: the chart also lists port 9617 on the DNS Service, so the metrics are readable
on the LAN at `10.0.40.203:9617`. Harmless on a LAN-only network, but not private.
The exporter logs in to Pi-hole each scrape, which shows in its session list.

## Changing the admin password

Update the Secret, then `kubectl -n pihole rollout restart deployment pihole`. The
exporter reads the same Secret, so it picks up the new password on restart too.

## Troubleshooting

- **EXTERNAL-IP stuck on `<pending>`**: the IP is outside the pool or already used
  (`kubectl get ciliumloadbalancerippool`), or the annotation is mistyped.
- **A pod is Pending**: the spread rule needs 3 schedulable workers. If one is down,
  the third pod waits until it returns (the other two still serve DNS).
- **Timeouts from some LAN devices only**: check
  `kubectl get ciliuml2announcementpolicy` and that the pods are on workers.
- **Is it spreading?** Run `for i in $(seq 50); do dig +short @10.0.40.203 example$i.com >/dev/null; done`,
  then compare `pihole_dns_queries_today` per `pod` in Prometheus: all three should grow.
