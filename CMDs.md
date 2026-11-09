# 1. Inject 2.5s latency:
**A. Prove the live microservice in Minikube is actually delayed:**
Invoke-RestMethod -Uri "http://127.0.0.1:8888/api/test-request"

**B. View real-time controller decision logs:**
kubectl logs -n healkube-demo deployment/healkube-controller --tail 20  

# live logs #
kubectl get pods -n healkube-demo -l app=demo-app -w
**C. Prove Kubernetes replaced the Pod (check the 'AGE' column - it will be seconds old):**
kubectl get pods -n healkube-demo -l app=demo-app

**D. View the official Kubernetes eviction & container recreation events:**
kubectl get events -n healkube-demo --sort-by=.metadata.creationTimestamp

# 2. Upstream Dependecy:
. # A. Verify upstream health failure reported in the telemetry snapshot:
(Invoke-RestMethod -Uri "http://127.0.0.1:8888/status").last_diagnosis

**B. Verify the pod restart count did NOT increment (pods were preserved):**
kubectl get pods -n healkube-demo -l app=demo-app

**C. Verify controller logs explicit refusal to restart:**
kubectl logs -n healkube-demo deployment/healkube-controller --tail 15

# 3.Traffic surge:
.# A. Check deployment replicas scaled from 1 -> 2:
kubectl get deployment demo-app -n healkube-demo

**B. View both active running Pod instances scheduled by Kubernetes:**
kubectl get pods -n healkube-demo -l app=demo-app