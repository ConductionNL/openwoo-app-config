# Egress-debug-runbook — Calico/Gardener DNS-breuk isoleren (openwoo-provisioner)

**Status: DRAFT — nog niet uitgevoerd.** Dit runbook is mensenwerk: elke stap
wordt door een operator gedraaid, niet door een agent. De stappen 3 en 5
(pod aanmaken, policy applyen) zijn cluster-mutaties.

## Context

- **Symptoom** (2026-07-13, tweede keer): na apply van de egress-policy
  faalde oauth2-proxy in de webgui-pod op `lookup iam.commonground.nu:
  i/o timeout` — DNS geblokkeerd óndanks een expliciete
  kube-system/53-regel (UDP+TCP).
- **Policy**: `openwoo-app-config/webgui/deploy/networkpolicy-egress.yaml`
  (naam `openwoo-provisioner-egress`), sinds 2026-07-13 uit
  `kustomization.yaml` gehaald; de ingress-policy `openwoo-provisioner`
  staat nog wél live.
- **Doelpod-labels**: `app.kubernetes.io/name=openwoo-provisioner` in
  namespace `openwoo-platform` (dit is de selector van beide policies).
- **Cluster-feiten** (gecheckt 2026-07-14, read-only):
  - kube-dns Service: `100.64.0.10` (53/UDP, 53/TCP) in `kube-system`
  - CoreDNS-pods (géén hostNetwork): 2 replica's, pod-IP's in `100.96.0.0/11`
    (momenteel `100.96.1.35` en `100.96.18.64` — **vluchtig, altijd opnieuw
    opvragen**, stap 0)
  - Géén node-local-dns DaemonSet in kube-system (wel apiserver-proxy,
    kube-proxy per worker-pool — Gardener-specifiek)

## Hypotheses

| # | Hypothese | Mechanisme | Onderscheidende uitkomst |
|---|---|---|---|
| H1 | **Service-DNAT** | Calico evalueert de egress-regel tegen het pre-DNAT-doel `100.64.0.10`. Dat IP is geen pod in kube-system, dus de `namespaceSelector kube-system`-regel matcht niet. | dig naar CoreDNS-**pod-IP** werkt, dig naar **Service-IP** faalt (mét policy). |
| H2 | **Selector** | De namespaceSelector-regel matcht om een andere reden niet (label-mapping, Calico-vertaling van de policy). | dig naar pod-IP **én** Service-IP falen beide (mét policy), terwijl een ipBlock-variant wél werkt. |
| H3 | **Andere resolver** | De pod resolvet helemaal niet via 100.64.0.10 (bv. node-local of Gardener-proxy op een link-local IP) — dan raakt geen enkele kube-system-regel het echte verkeer. | `/etc/resolv.conf` in de pod wijst níet naar 100.64.0.10. Goedkoopste check, daarom stap 2 eerst. |

## Stap 0 — voorbereiding (read-only)

Actuele CoreDNS-pod-IP's ophalen en noteren:

kubectl get pods -n kube-system -l k8s-app=kube-dns -o wide

Bevestig de uitgangssituatie — alleen de ingress-policy mag live staan:

kubectl get networkpolicy -n openwoo-platform

Verwacht: alléén `openwoo-provisioner`. Staat `openwoo-provisioner-egress`
er al, dan is de uitgangssituatie vervuild: eerst verwijderen (stap 6) en
opnieuw beginnen.

Noteer in dit runbook: datum/tijd, pod-IP's van CoreDNS (hierna `<POD_IP_1>`
en `<POD_IP_2>`).

## Stap 1 — debug-pod starten (mutatie: pod aanmaken)

Zelfde namespace, zelfde labels als de webgui-pod, zodat élke policy die op
de webgui grijpt ook op deze pod grijpt:

kubectl run egress-debug -n openwoo-platform --restart=Never --labels="app.kubernetes.io/name=openwoo-provisioner" --image=nicolaka/netshoot:latest -- sleep 3600

(Alternatief image als netshoot niet mag: `registry.k8s.io/e2e-test-images/jessie-dnsutils:1.7` — heeft dig en getent.)

Wachten tot Running:

kubectl get pod egress-debug -n openwoo-platform -w

Let op: de pod erft door de labels ook de bestaande **ingress**-policy;
dat is bedoeld en irrelevant voor egress-tests (policyTypes Ingress
beperkt geen uitgaand verkeer).

Alle testcommando's hieronder via:

kubectl exec -n openwoo-platform egress-debug -- <commando>

## Stap 2 — resolver-identiteit (test H3, goedkoopst eerst)

kubectl exec -n openwoo-platform egress-debug -- cat /etc/resolv.conf

- **Verwacht (H3 verworpen):** `nameserver 100.64.0.10`. Ga door naar stap 3.
- **H3 bevestigd:** een ander IP (bv. 169.254.x.x of een node-IP). Dan is de
  fix een egress-regel naar dát IP/53 en is de rest van dit runbook
  secundair. Noteer het IP en stop hier eventueel al.

## Stap 3 — baseline ZONDER egress-policy

Alle vier moeten slagen; zo niet, dan is er een probleem búiten de policy
en heeft verder testen geen zin.

| # | Commando | Verwacht |
|---|---|---|
| 3a | `dig +time=3 +tries=1 @100.64.0.10 kubernetes.default.svc.cluster.local` | NOERROR, antwoord < 1 s |
| 3b | `dig +time=3 +tries=1 @<POD_IP_1> kubernetes.default.svc.cluster.local` | NOERROR |
| 3c | `dig +tcp +time=3 +tries=1 @100.64.0.10 kubernetes.default.svc.cluster.local` | NOERROR (TCP-pad) |
| 3d | `getent hosts iam.commonground.nu` | IP-adres terug (het exacte symptoom-domein van 2026-07-13) |

Extra referentiepunt (extern 443, los van DNS):

kubectl exec -n openwoo-platform egress-debug -- curl -sS -o /dev/null -w '%{http_code}\n' --max-time 5 https://iam.commonground.nu

Verwacht: een HTTP-statuscode (2xx/3xx/4xx maakt niet uit — connectiviteit telt).

## Stap 4 — egress-policy applyen (mutatie, mensenwerk)

**Niet via Argo/kustomization** — bewust een losse, direct terugdraaibare apply:

kubectl apply -f /home/gongoeloe/CONDUCTION/openwoo-app-config/webgui/deploy/networkpolicy-egress.yaml

Controle:

kubectl get networkpolicy openwoo-provisioner-egress -n openwoo-platform

Wacht ~10 s (Calico-propagatie naar de node-dataplane).

**Rollback staat klaar** (op elk moment):

kubectl delete networkpolicy openwoo-provisioner-egress -n openwoo-platform

De debug-pod vangt de klap, niet de webgui: de echte webgui-pod ondervindt
de policy óók (zelfde labels), dus voer dit uit in een rustig venster en
houd de rollback-regel binnen handbereik.

## Stap 5 — testmatrix MÉT policy (de eigenlijke meting)

Zelfde commando's als stap 3, zelfde volgorde. Interpretatie:

| 5a Service-IP UDP | 5b pod-IP UDP | 5c Service-IP TCP | Conclusie |
|---|---|---|---|
| FAALT (timeout) | WERKT | FAALT | **H1 bevestigd**: pre-DNAT-evaluatie; de selector-regel ziet 100.64.0.10 en matcht niet. |
| FAALT | FAALT | FAALT | **H2**: selector matcht überhaupt niet → door naar stap 5-bis. |
| WERKT | WERKT | WERKT | Breuk niet gereproduceerd — mogelijk timing/conntrack bij de oorspronkelijke apply (bestaande verbindingen), of het symptoom zat in een ander pad (oauth2-proxy start-timing). Test dan 3d/curl opnieuw en herstart-scenario overwegen. |
| WERKT | WERKT | FAALT | TCP-53-specifiek; check of de TCP-poortregel goed in de policy staat (staat er wel; dan Calico-bug-verdenking, noteren). |

En altijd óók: `getent hosts iam.commonground.nu` (het echte symptoom) en de
curl uit stap 3 (bewijst dat 443-egress als zodanig werkt, dus dat een
failure écht DNS is).

### Stap 5-bis — alleen bij H2: ipBlock-differentiatie (mutatie: policy-edit op de LIVE-COPY, niet in git)

Doel: bewijzen dat de selector (niet de poort/het pad) het probleem is.
Voeg **tijdelijk, buiten git om** een ipBlock-regel toe aan de geappliede
policy (dekt Service-IP én pod-CIDR):

kubectl edit networkpolicy openwoo-provisioner-egress -n openwoo-platform

Toe te voegen egress-regel (zelfde poorten 53 UDP+TCP):

  - to:
      - ipBlock: { cidr: 100.64.0.10/32 }
      - ipBlock: { cidr: 100.96.0.0/11 }
    ports:
      - { protocol: UDP, port: 53 }
      - { protocol: TCP, port: 53 }

Hertest 5a/5b:
- **Werkt nu wél** → selector-mechanisme kapot, ipBlock is de workaround;
  H1 vs H2 verder scheiden: haal `100.96.0.0/11` weg — werkt Service-IP dan
  nog steeds, dan volstond het Service-IP-block (sterk H1-signaal alsnog).
- **Werkt nog steeds niet** → probleem zit dieper (dataplane), escaleren
  met alle metingen; verdenking Calico/kube-proxy-interactie per
  worker-pool (Gardener draait aparte kube-proxy-DaemonSets per pool —
  test desnoods de debug-pod op een andere pool via nodeSelector).

## Stap 6 — opruimen (mutatie)

Altijd, ook bij afbreken:

kubectl delete networkpolicy openwoo-provisioner-egress -n openwoo-platform
kubectl delete pod egress-debug -n openwoo-platform

Controle terug naar uitgangssituatie:

kubectl get networkpolicy -n openwoo-platform
kubectl get pods -n openwoo-platform

Verwacht: alléén policy `openwoo-provisioner`, alléén de webgui-pod.

## Stap 7 — bevindingen vastleggen

Resultaatmatrix + conclusie bijschrijven in het BEVINDING-blok van
`openwoo-app-config/webgui/deploy/networkpolicy-egress.yaml` (zelfde
plek als de 2026-07-13-notitie), inclusief datum, CoreDNS-pod-IP's en
kube-proxy-mode indien vastgesteld. Pas dáárna besluiten of de policy
(eventueel met ipBlock-fix) terug de kustomization in gaat — via PR,
niet direct.

## Bekende valkuilen

- **Pod-IP's van CoreDNS wisselen** — altijd stap 0 herhalen na een
  CoreDNS-herstart.
- **Conntrack**: bestaande DNS-flows kunnen een net geappliede policy
  overleven of juist net gekilld zijn; daarom een vérse debug-pod en
  `+tries=1` zodat een timeout een echte timeout is.
- **De webgui-pod deelt de labels**: elke policy-apply in stap 4/5-bis
  raakt ook productie-webgui. Kort venster, rollback paraat.
- **Geen `--rm -it`** gebruikt bij `kubectl run`: bewust, zodat de pod
  blijft staan tussen metingen; opruimen is expliciet stap 6.
