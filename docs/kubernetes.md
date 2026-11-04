# Deploying the API and web app on Kubernetes

The API and web app run on a small Kubernetes cluster you build yourself with kubeadm on two EC2
servers. The document workers, tables, queue and bucket stay where they are, in the `Application`
and `JobPipeline` stacks.

```
Browser → https://app.yourdomain.com → DNS A record → worker's Elastic IP
  → ports 80/443 on the worker → Traefik (HTTPS, routing)
       ├─ /api/* → api pod (FastAPI + TeX, 1 copy) → S3, DynamoDB, SQS, Bedrock via the worker's IAM role
       └─ /*     → web pod (nginx with the built React app)
```

| Piece | Where |
|---|---|
| Servers, network, IAM roles, image repositories | `infra/cluster_stack.py` (`Cluster` stack) |
| What each server installs on first boot | `infra/cluster/node-setup.sh` |
| Ingress controller settings | `deploy/cluster/traefik-values.yaml` |
| The app | Helm chart in `deploy/helm/job-applier` |
| Images | `containers/Dockerfile.api`, `containers/Dockerfile.web` |

`.github/workflows/cluster.yml` deploys the `Cluster` stack and pushes the API and web images
after CI/CD or Frontend succeeds on a push to `main` (see [ci-cd.md](ci-cd.md)). Building the
cluster and `helm upgrade` stay manual: GitHub cannot reach the cluster, whose API port is closed.
Steps 1 and 2 below do by hand what that workflow does.

## What it costs

Roughly $45-55 a month while the servers run: a `t3.small` control plane, a `t3.medium` worker,
two 30 GB disks and two public IPv4 addresses. Check current EC2 prices before you start.
Stopping both servers stops the server charges, but not the disks or the Elastic IP. Bedrock
tailoring is charged per use, as before.

## What you need first

- An AWS login in the terminal: `aws login`, then `aws sts get-caller-identity`.
- On your Mac: `brew install helm kubectl` and
  `brew install --cask session-manager-plugin` (for `aws ssm start-session`).
- A domain name, or the free `sslip.io` name described in step 7 to try it first.

## 1. Create the servers

```sh
JSII_SILENCE_WARNING_UNTESTED_NODE_VERSION=1 npx cdk deploy Cluster
```

Note the outputs: `ControlPlaneInstanceId`, `ControlPlanePrivateIp`, `WorkerInstanceId`,
`WorkerPublicIp`, `ApiRepositoryUri` and `WebRepositoryUri`. To see them again:

```sh
aws cloudformation describe-stacks --stack-name Cluster \
  --query "Stacks[0].Outputs[].[OutputKey,OutputValue]" --output table
```

Each server now runs `node-setup.sh` once, which takes a few minutes. It installs containerd,
kubeadm, kubelet, kubectl, the ECR credential plugin and Helm, and nothing else.

## 2. Build and push the images

The servers are `amd64`, so build for it even on an Apple Silicon Mac. ECR tags cannot be
reused, so tag with the commit.

```sh
REGISTRY=$(aws sts get-caller-identity --query Account --output text).dkr.ecr.us-east-1.amazonaws.com
TAG=$(git rev-parse --short HEAD)
aws ecr get-login-password | docker login --username AWS --password-stdin "$REGISTRY"

docker build --platform linux/amd64 -f containers/Dockerfile.api -t "$REGISTRY/cluster/api:$TAG" .
docker build --platform linux/amd64 -f containers/Dockerfile.web -t "$REGISTRY/cluster/web:$TAG" .
docker push "$REGISTRY/cluster/api:$TAG"
docker push "$REGISTRY/cluster/web:$TAG"
```

## 3. Create the cluster on the control plane

Open a shell on the control plane. Systems Manager connects you without SSH, so no port 22 is
open:

```sh
aws ssm start-session --target <ControlPlaneInstanceId>
```

Then, on the server:

```sh
sudo -i                                  # Everything below runs as root.
cloud-init status --wait                 # Waits for node-setup.sh; "status: done" is good.
cat /var/log/node-setup.done             # "node-setup finished"

# Create the control plane. Calico expects pods in 192.168.0.0/16, which must not overlap the
# VPC (10.20.0.0/16). 127.0.0.1 is added to the API server's certificate so that kubectl can
# reach it through a Systems Manager tunnel from your Mac (step 6).
kubeadm init --pod-network-cidr=192.168.0.0/16 --apiserver-cert-extra-sans=127.0.0.1

mkdir -p ~/.kube && cp /etc/kubernetes/admin.conf ~/.kube/config
kubectl get nodes                        # NotReady until there is a pod network.

# The pod network: Calico, through its operator.
kubectl create -f https://raw.githubusercontent.com/projectcalico/calico/v3.33.0/manifests/tigera-operator.yaml
kubectl create -f https://raw.githubusercontent.com/projectcalico/calico/v3.33.0/manifests/custom-resources.yaml
kubectl get pods -A -w                   # Wait until calico-system pods are Running; Ctrl-C.
kubectl get nodes                        # Ready.

# The command the worker runs to join, valid for 24 hours.
kubeadm token create --print-join-command
```

The control plane keeps its `NoSchedule` taint, so your pods run on the worker, which is the
server with the app's IAM role and the Elastic IP.

## 4. Join the worker

```sh
aws ssm start-session --target <WorkerInstanceId>
sudo -i
cloud-init status --wait
kubeadm join ...                          # The full command printed in step 3.
```

Back on the control plane, `kubectl get nodes` shows both, `Ready` within a minute.

## 5. Install Traefik and cert-manager

On the control plane, where Helm is installed. Copy the Traefik values over first: in the
control-plane session, run `cat > traefik-values.yaml`, paste the contents of
`deploy/cluster/traefik-values.yaml`, then press Ctrl-D.

```sh
helm repo add traefik https://traefik.github.io/charts
helm install traefik traefik/traefik --version 42.3.0 \
  -n traefik --create-namespace -f traefik-values.yaml --wait

helm install cert-manager oci://quay.io/jetstack/charts/cert-manager --version v1.21.3 \
  -n cert-manager --create-namespace --set crds.enabled=true --wait
```

Traefik listens on the worker's ports 80 and 443 directly, so no AWS load balancer is needed.
Sending HTTP to HTTPS is done by Traefik for every site on the cluster.

## 6. Use kubectl and Helm from your Mac

The API server's port 6443 is not open to the internet. Systems Manager forwards it instead.

1. On the control plane: `cat /etc/kubernetes/admin.conf`. Copy the output into
   `~/.kube/job-applier.yaml` on your Mac and change the `server:` line to
   `server: https://127.0.0.1:6443`. This file is a full admin login to the cluster: keep it out
   of the repository and out of chats.
2. In a separate terminal on the Mac, keep this running:
   ```sh
   aws ssm start-session --target <ControlPlaneInstanceId> \
     --document-name AWS-StartPortForwardingSession \
     --parameters portNumber=6443,localPortNumber=6443
   ```
3. Then: `export KUBECONFIG=~/.kube/job-applier.yaml` and `kubectl get nodes`.

## 7. Point the domain at the worker

Create an **A record** from your app's name (for example `app.yourdomain.com`) to `WorkerPublicIp`,
in Route 53 or at your registrar. Check it with `dig +short app.yourdomain.com`.

Without a domain, `sslip.io` names resolve to the IP written in them, and Let's Encrypt can issue
certificates for them: for the IP `3.90.1.2`, use `app.3-90-1-2.sslip.io`. That is fine for
trying the setup. Use a domain you own for anything you keep, as the name belongs to a third
party and changes with the IP.

## 8. Allow the new address in Auth0

In the Auth0 application's settings, add these next to the localhost entries (comma-separated):

- **Allowed Callback URLs:** `https://app.yourdomain.com/api/auth/callback`
- **Allowed Logout URLs** and **Allowed Web Origins:** `https://app.yourdomain.com`

The chart sets `AUTH0_APP_URL` and `AUTH0_CALLBACK_URL` from the host itself, so they don't go
in the secret.

## 9. Give the API its settings

The values from `.env` go into a Kubernetes Secret created by hand, never into the chart or Git.
From the repository on your Mac:

```sh
kubectl create namespace job-applier
grep -E '^(AUTH0_DOMAIN|AUTH0_CLIENT_ID|AUTH0_CLIENT_SECRET|AUTH0_OWNER_ID)=' .env > /tmp/api.env
kubectl -n job-applier create secret generic job-applier --from-env-file=/tmp/api.env
rm /tmp/api.env
```

**Mail:** to track mail from the cluster, add `MAIL_` to the pattern. Then stop running the
local API with the same `.env`, as two APIs syncing one mailbox would read it twice. Without the
`MAIL_*` values the deployed app works, and the mail bar says mail is not set up.

To change a value later, delete and recreate the secret, then
`kubectl -n job-applier rollout restart deploy/api`, as the API only reads it at start-up.

## 10. Install the app

Start with Let's Encrypt's **staging** server. Its certificates are not trusted by browsers,
but mistakes there don't use up the production limits.

```sh
helm install job-applier deploy/helm/job-applier -n job-applier \
  --set host=app.yourdomain.com \
  --set tls.email=you@example.com \
  --set tls.staging=true \
  --set images.api.repository=$REGISTRY/cluster/api --set images.api.tag=$TAG \
  --set images.web.repository=$REGISTRY/cluster/web --set images.web.tag=$TAG

kubectl -n job-applier get pods -w        # api and web Running and 1/1; Ctrl-C.
kubectl -n job-applier get certificate    # READY True after a minute or two.
```

When the certificate is ready, switch to a real one:

```sh
helm upgrade job-applier deploy/helm/job-applier -n job-applier --reuse-values --set tls.staging=false
kubectl -n job-applier delete secret job-applier-tls   # So cert-manager asks again.
```

Open `https://app.yourdomain.com`. It sends you to the Auth0 login and back.

## Updating the app

Build and push with a new `TAG` (step 2), then:

```sh
helm upgrade job-applier deploy/helm/job-applier -n job-applier --reuse-values \
  --set images.api.tag=$TAG --set images.web.tag=$TAG
```

The API stops before its replacement starts, so it is down for a few seconds. The deploy order
in `CLAUDE.md` applies here too: let CI deploy the document worker before upgrading the API.

## When something is wrong

| Symptom | Look at |
|---|---|
| A pod is not `Running` | `kubectl -n job-applier describe pod <name>`: the Events at the end |
| `ErrImagePull` from ECR | The tag exists in ECR; on the worker, `journalctl -u kubelet \| grep -i credential` |
| The API restarts | `kubectl -n job-applier logs deploy/api --previous` |
| "Unable to locate credentials" in the API log | The worker's metadata hop limit is 2: `aws ec2 describe-instances --instance-ids <WorkerInstanceId> --query "Reservations[0].Instances[0].MetadataOptions"` |
| The certificate stays not ready | `kubectl -n job-applier describe certificate,certificaterequest,order,challenge`; usually the DNS record does not point at the worker yet |
| A node is `NotReady` | On it: `systemctl status kubelet containerd`, `journalctl -u kubelet -e` |
| The browser shows a 404 page from Traefik | The host you typed is not the chart's `host` |

## Taking it down

```sh
helm uninstall job-applier -n job-applier             # The app only.
npx cdk destroy Cluster                                # Both servers, the network and the IP.
```

Your data is in S3 and DynamoDB, which this stack does not hold. The two image repositories are
kept on destroy, so delete them in the ECR console if you no longer want them.

## Not done yet

- **One control plane:** if it fails, the app keeps running for a while but the cluster can't be
  managed or changed. Rebuilding it means repeating steps 3-10.
- **Certificate expiry:** kubeadm's own certificates expire after a year.
  `kubeadm certs check-expiration` shows when; `kubeadm upgrade` renews them, or
  `kubeadm certs renew all`.
- **etcd backups:** `etcdctl snapshot save` on the control plane. The cluster holds no app data,
  but it holds the secrets and certificates.
- **Upgrading Kubernetes:** packages are held at 1.37. Upgrading is `kubeadm upgrade`, one minor
  version at a time, control plane first.
- **Deploying from CI:** CI pushes the images but `helm upgrade` is manual, as GitHub cannot
  reach the cluster. A tool running inside the cluster, such as Argo CD or Flux, could apply
  changes from Git instead.
- **Pod permissions:** every pod on the worker can act as its IAM role. A role per pod needs a
  pod identity webhook and an OIDC issuer.
- **Spending limits:** a user can start any number of tailorings. Create accounts only for people
  you trust.
- **Compiling LaTeX in the API:** a user's edited CV is compiled inside the API pod, with the
  limits in `render.py` but no stronger isolation (see `skipped-work.md`).
