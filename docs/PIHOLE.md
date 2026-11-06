# Pi-hole

Continues from [ARGOCD.md](ARGOCD.md) (Cilium hands out the LAN IP). Runs three
independent Pi-holes, each with its own LAN IP.

| Pi-hole | DNS | Web UI |
| --- | --- | --- |
| `pihole-1` | `10.0.40.203` | <http://10.0.40.204/admin> |
| `pihole-2` | `10.0.40.205` | <http://10.0.40.206/admin> |
| `pihole-3` | `10.0.40.207` | <http://10.0.40.208/admin> |

## How it works

```
LAN device -> 10.0.40.203 / .205 / .207 :53 (one Cilium LB IP each, UDP+TCP)
           -> that Pi-hole's pod (the three pods are on different workers)
                                  |
                   blocked? answer 0.0.0.0 : forward to 1.1.1.1
```

- [apps/pihole.yaml](../apps/pihole.yaml): an ApplicationSet that installs the `pihole`
  chart from <https://mojo2600.github.io/pihole-kubernetes/> (version pinned) three
  times, as releases `pihole-1`, `pihole-2`, `pihole-3`. The only per-Pi-hole settings,
  the two IPs, are listed here.
- [infrastructure/pihole/values.yaml](../infrastructure/pihole/values.yaml): all
  settings, commented.
- Each release is one Deployment with 1 pod, spread across workers. Each has its own
  `LoadBalancer` Services with IPs pinned by `lbipam.cilium.io/ips`; Cilium answers
  ARP for an IP from the worker that runs that pod (`externalTrafficPolicy: Local`).
  A Deployment cannot give each of its replicas its own Service, which is why these
  are three releases rather than `replicaCount: 3`.
- To add a fourth, add an entry to the list in `apps/pihole.yaml` with two free IPs
  from the pool (and note the spread rule then needs 4 workers).
- The three Pi-holes do not talk to each other. They are identical only because they
  are configured from the same file.

## Trade-offs to know about

- **No persistent volume.** The query log and anything changed in the web UI reset
  when a pod restarts. Block lists, upstream DNS and so on belong in the values
  file. This is what lets three pods run on three nodes without sharing a volume.
- **Dashboards are per Pi-hole.** A query only shows up on the Pi-hole whose IP the
  client asked. Each has its own web UI IP (table above).
- **No automatic load balancing or failover.** A client only fails over if it is
  given several DNS servers, so hand out all three IPs (step 4). Some devices use
  only the first one or two.
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
kubectl -n argocd get applications | grep pihole   # pihole-1, -2, -3
kubectl -n pihole get pods -o wide        # 3 Running, each on a different node
kubectl -n pihole get svc                 # each pihole-N-... has its own EXTERNAL-IP
```

### 3. Test it

```bash
for ip in 10.0.40.203 10.0.40.205 10.0.40.207; do
  dig @$ip example.com +short             # resolves
  dig @$ip doubleclick.net +short         # 0.0.0.0 = blocked
done
```

Failover: `kubectl -n pihole delete pod <one pod>`; that IP is briefly down and the
other two keep answering.

### 4. Point the LAN at it

In the router's DHCP settings, set the DNS servers to `10.0.40.203`, `10.0.40.205`
and `10.0.40.207` (if the router only takes two, use two). Adding a non-Pi-hole
server (e.g. `1.1.1.1`) would let browsing survive a full outage, at the cost of some
devices bypassing the blocking.

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

On the LAN: see the table at the top (or https, accepting the certificate warning).
Log in with the password from step 1 (all three share it). The other two are at
`10.0.40.206` and `10.0.40.208`. Or use a port-forward:

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
on the LAN at each DNS IP, e.g. `10.0.40.203:9617`. Harmless on a LAN-only network, but not private.
The exporter logs in to Pi-hole each scrape, which shows in its session list.

## Changing the admin password

Update the Secret, then
`for n in 1 2 3; do kubectl -n pihole rollout restart deployment pihole-$n; done`. The
exporter reads the same Secret, so it picks up the new password on restart too.

## Troubleshooting

- **EXTERNAL-IP stuck on `<pending>`**: the IP is outside the pool or already used
  (`kubectl get ciliumloadbalancerippool`), or the annotation is mistyped.
- **A pod is Pending**: the spread rule needs 3 schedulable workers. If one is down,
  the third pod waits until it returns (the other two still serve DNS).
- **Timeouts from some LAN devices only**: with `externalTrafficPolicy: Local`, the
  announcing node must have a pod. Check
  `kubectl get ciliuml2announcementpolicy` and that the pods are on workers.
- **Moving from the old single-IP setup**: the old `pihole` app is replaced by
  `pihole-1`..`3`. Delete the old `pihole` Application in Argo CD if it lingers; it
  holds `.203` until it is gone, so `pihole-1` may sit on `<pending>` briefly.
