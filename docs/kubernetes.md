# Deploying the API and web app on Kubernetes

The API and web app run on a small Kubernetes cluster you build yourself with kubeadm on three EC2
servers: a control plane and two workers, behind an AWS Network Load Balancer. The workers are an
EC2 Auto Scaling group: they join the cluster by themselves, and how many run is set on the
group itself, by hand, never by a deploy. The document
workers, tables, queue and bucket stay where they are, in the `Application` and `JobPipeline`
stacks.

```
Browser → https://app.yourdomain.com → DNS A record → the load balancer's Elastic IP
  → Network Load Balancer (plain TCP, health checks) → node port 30080/30443 on a worker
  → Traefik on that worker (HTTPS, routing; one copy per worker)
       ├─ /api/* → api pod (FastAPI + TeX, 1 copy) → S3, DynamoDB, SQS, Bedrock via the workers' IAM role
       └─ /*     → web pod (nginx with the built React app)
```

The api and web pods can be on either worker: Traefik reaches them over the pod network. If a
worker stops, the balancer's health check notices within about 20 seconds and sends everything to
the other one.

| Piece | Where |
|---|---|
| Servers, network, IAM roles, image repositories | `infra/cluster_stack.py` (`Cluster` stack) |
| What each server installs on first boot | `infra/cluster/control-plane.sh`, `infra/cluster/worker.sh` (also joins) |
| The join command the workers read | `scripts/publish_join_command.sh`, into Parameter Store |
| Ingress controller settings | `deploy/cluster/traefik-values.yaml` |
| The app | Helm chart in `deploy/helm/job-applier` |
| Images | `containers/Dockerfile.api`, `containers/Dockerfile.web` |

`.github/workflows/ci-cd.yml` pushes the API and web images to the `Repositories` stack and then
deploys the `Cluster` stack after the other two on a push to `main` (see [ci-cd.md](ci-cd.md)). Building the
cluster and `helm upgrade` stay manual: GitHub cannot reach the cluster, whose API port is closed.
Steps 1 and 2 below do by hand what that workflow does.

## What it costs

Roughly $115-125 a month while the servers run: a `t3.medium` control plane (a `t3.small`, with
2 GB, was too small for etcd and the API server), two `t3.medium` workers, three 30 GB disks,
four public IPv4 addresses and the Network Load Balancer (about $16-20 of that). Check current
prices before you start. Stopping the servers stops the server charges, but not the disks, the
load balancer or the Elastic IP (see "Pausing the cluster"). Bedrock tailoring is charged per
use, as before.

## What you need first

- An AWS login in the terminal: `aws login`, then `aws sts get-caller-identity`.
- On your Mac: `brew install helm kubectl` and
  `brew install --cask session-manager-plugin` (for `aws ssm start-session`).
- A domain name, or the free `sslip.io` name described in step 7 to try it first.

## 1. Create the servers

```sh
JSII_SILENCE_WARNING_UNTESTED_NODE_VERSION=1 npx cdk deploy Cluster
```

Note the outputs: `ControlPlaneInstanceId`, `ControlPlanePrivateIp`, `WorkerGroupName` and
`PublicIp` (the load balancer's address). To see them again:

```sh
aws cloudformation describe-stacks --stack-name Cluster \
  --query "Stacks[0].Outputs[].[OutputKey,OutputValue]" --output table
```

Each server now runs its setup script once, which takes a few minutes. Both install containerd,
kubeadm, kubelet, kubectl and the ECR credential plugin. `control-plane.sh` adds Helm and stops
there. `worker.sh` adds the AWS CLI, turns off the server's source/dest check (which a launch
template cannot do) and then tries every minute to join the cluster, which works once step 4 is
done. To see the workers:

```sh
aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names <WorkerGroupName> \
  --query "AutoScalingGroups[0].Instances[].[InstanceId,LifecycleState,HealthStatus]" --output table
```

## 2. Build and push the images

The repositories are in the `Repositories` stack (`npx cdk deploy Repositories`, once; outputs
`ApiRepositoryUri` and `WebRepositoryUri`). The servers are `amd64`, so build for it even on an Apple Silicon Mac. ECR tags cannot be
reused, so tag with the commit.

```sh
REGISTRY=$(aws sts get-caller-identity --query Account --output text).dkr.ecr.us-east-1.amazonaws.com
TAG=$(git rev-parse --short HEAD)
aws ecr get-login-password | docker login --username AWS --password-stdin "$REGISTRY"

docker build --platform linux/amd64 -f containers/Dockerfile.api -t "$REGISTRY/job-applier/api:$TAG" .
docker build --platform linux/amd64 -f containers/Dockerfile.web -t "$REGISTRY/job-applier/web:$TAG" .
docker push "$REGISTRY/job-applier/api:$TAG"
docker push "$REGISTRY/job-applier/web:$TAG"
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
cloud-init status --wait                 # Waits for control-plane.sh; "status: done" is good.
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
```

The control plane keeps its `NoSchedule` taint, so your pods run on the workers, which have the
app's IAM role. The load balancer sends traffic to the workers only.

## 4. Let the workers join

On your Mac:

```sh
scripts/publish_join_command.sh           # A join command valid for 24 hours.
aws autoscaling set-desired-capacity --auto-scaling-group-name <WorkerGroupName> \
  --desired-capacity 2                    # A new stack starts with no workers.
```

It runs `kubeadm token create --print-join-command` on the control plane through Systems Manager
and saves the result as the SecureString parameter `/job-applier/cluster/join-command`, which only
the workers' role and your admin login can read. The token never shows in your terminal. Each
worker picks it up within a minute; back on the control plane, `kubectl get nodes` shows all three,
`Ready` soon after.

A worker started after the token expires keeps trying until there is a valid one, so run the
script again before you add workers or resume the cluster. `scripts/publish_join_command.sh 0`
publishes a token that never expires, so that any new worker can join with nothing to run, at a
cost: anyone who can read it and reach the control plane's port 6443 (only the nodes can) can add
a server to the cluster, and the pods on the workers can read it, as they act as the workers' role.

On a worker that does not join, open a shell (`aws ssm start-session --target <InstanceId>`, the
instance ID from the command above) and read `/var/log/cloud-init-output.log`.

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

Traefik runs one copy on each worker, behind a `NodePort` Service on ports 30080 and 30443, which
are the load balancer's targets. Its `externalTrafficPolicy: Local` makes each worker pass that
traffic only to its own Traefik copy, so a worker without one fails the health check and gets no
traffic. HTTPS ends at Traefik, with cert-manager's certificate; the balancer only passes TCP
through. Sending HTTP to HTTPS is done by Traefik for every site on the cluster.

`kubectl -n traefik get pods -o wide` shows one Traefik pod per worker. In the EC2 console, under
**Target groups**, both workers should be `healthy` on both ports.

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

## 7. Point the domain at the load balancer

Create an **A record** from your app's name (for example `app.yourdomain.com`) to `PublicIp`,
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
  --set images.api.repository=$REGISTRY/job-applier/api --set images.api.tag=$TAG \
  --set images.web.repository=$REGISTRY/job-applier/web --set images.web.tag=$TAG

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

CI prints this command with its tags in the run's summary. By hand, build and push with a new
`TAG` (step 2), then:

```sh
helm upgrade job-applier deploy/helm/job-applier -n job-applier --reuse-values \
  --set images.api.tag=$TAG --set images.web.tag=$TAG
```

The API stops before its replacement starts, so it is down for a few seconds. The deploy order
in `CLAUDE.md` applies here too: let CI deploy the document worker before upgrading the API.

## Moving to the Repositories stack

The images were in `cluster/api` and `cluster/web`, created by this stack. They are now in
`job-applier/api` and `job-applier/web`, so switch the running app once, with the `helm upgrade`
command from the summary of the first CI run after the move. It sets the repositories as well as
the tags. Nothing on the servers changes: the ECR plugin takes any repository of the account.
The old repositories stay in ECR, no longer in any stack; delete them in the console when the
app runs from the new ones.

## Moving to the load balancer

Only for a cluster built before the load balancer, where Traefik listened on the worker's own
ports 80 and 443 and the Elastic IP was on that worker. The site is down for a few minutes.

1. Join the second worker (step 4).
2. Push, so that CI deploys the `Cluster` stack. The Elastic IP moves to the load balancer, and
   the site stops answering until step 3.
3. Copy `deploy/cluster/traefik-values.yaml` to the control plane and run:
   ```sh
   helm upgrade traefik traefik/traefik --version 42.3.0 -n traefik -f traefik-values.yaml
   kubectl -n traefik get pods -o wide -w    # One Traefik per worker, both 1/1.
   ```
4. Check that both workers are `healthy` in the target groups, and that the first worker got a
   new public IP, which it needs to reach AWS.

## Moving to the worker group

Only for a cluster built when the workers were two separate servers (`Worker`, `Worker2`). Those
servers are deleted and two new ones join instead; the control plane and everything on it stay.

1. Start the control plane only, and wait for `kubectl get nodes` to answer (through the tunnel).
2. `scripts/publish_join_command.sh`
3. Push, so that CI deploys the `Cluster` stack: it terminates the old workers and creates the
   group, which starts empty. Set its size to 2 (the command in step 4); both new workers join
   within a few minutes.
4. Remove the old workers from the cluster. Until then their pods stay `Terminating`, and the API,
   which waits for its old copy to go, does not start:
   ```sh
   kubectl get nodes                        # The old ones are NotReady.
   kubectl delete node <old worker> <old worker>
   kubectl -n traefik get pods -o wide -w   # One Traefik per new worker.
   kubectl -n job-applier get pods -w       # api and web 1/1.
   ```

## Removing workers

Kubernetes never learns that a server is gone: with no cloud controller manager, which EKS would
run for you, a terminated worker stays in `kubectl get nodes` as `NotReady`, and its pods stay
`Terminating`. So after fewer workers, or a worker replaced by the group, delete the ones that
are gone:

```sh
kubectl get nodes --no-headers | awk '$2 ~ /NotReady/ {print $1}' | xargs -r kubectl delete node
```

To take workers away while the site runs, first move the pods off the ones that will go:
`kubectl drain <node> --ignore-daemonsets --delete-emptydir-data`. The group chooses which server
to terminate, so drain the one it will remove, or drain and delete it, then lower the number.

The number of workers is set on the group itself, with `set-desired-capacity` as above or in
the console (at most 4); the stack holds no number, so deploys never change it. Never stop a
worker: the group sees a stopped server as unhealthy and replaces it. Traefik runs two copies, so a third or fourth worker gets pods but no website traffic (it fails
the health check, as intended). A change to `worker.sh` reaches only workers started after it.

## Pausing the cluster

A load balancer cannot be stopped, only deleted: it costs about $16-20 a month even while the
servers are stopped. The `website` switch removes it from the stack, so CloudFormation deletes it
properly; never delete it by hand, as the stack would still believe it exists. The Elastic IP
stays, so the address, the DNS name and the certificate are the same when it comes back. CI reads
the switch from the repository variable `CLUSTER_WEBSITE` (`on` when unset).

The workers are not stopped but removed, with the group's size set to 0, and new ones join
when it is set back. Nothing is lost: the pods' definitions are on the control plane, and the app's
data in S3 and DynamoDB.

To pause:

```sh
aws autoscaling set-desired-capacity --auto-scaling-group-name <WorkerGroupName> \
  --desired-capacity 0                       # removes the workers
gh variable set CLUSTER_WEBSITE --body off
gh workflow run ci-cd.yml --ref main         # deletes the load balancer
gh run watch                                  # wait for it to finish
aws ec2 stop-instances --instance-ids <ControlPlaneInstanceId>
```

To resume:

```sh
aws ec2 start-instances --instance-ids <ControlPlaneInstanceId>
aws ec2 wait instance-status-ok --instance-ids <ControlPlaneInstanceId>
scripts/publish_join_command.sh               # the last token has most likely expired
aws autoscaling set-desired-capacity --auto-scaling-group-name <WorkerGroupName> \
  --desired-capacity 2                         # two fresh workers, which join by themselves
gh variable set CLUSTER_WEBSITE --body on
gh workflow run ci-cd.yml --ref main         # creates the load balancer again
gh run watch
```

Then, through the tunnel, delete the old workers, which are `NotReady` (the command in
"Removing workers"), and wait until the new ones are `Ready` and `healthy` in the target groups.
While the variable is `off`, pushes to `main` keep the load balancer away; a deploy from your
own machine needs `-c website=off` too. The workers need no variable: deploys leave the group's
size alone.

## When something is wrong

| Symptom | Look at |
|---|---|
| A pod is not `Running` | `kubectl -n job-applier describe pod <name>`: the Events at the end |
| `ErrImagePull` from ECR | The tag exists in ECR; on the worker, `journalctl -u kubelet \| grep -i credential` |
| The API restarts | `kubectl -n job-applier logs deploy/api --previous` |
| "Unable to locate credentials" in the API log | The worker's metadata hop limit is 2: `aws ec2 describe-instances --instance-ids <worker's InstanceId> --query "Reservations[0].Instances[0].MetadataOptions"` |
| A new worker never joins | On it, `/var/log/cloud-init-output.log`: "Could not join yet" means no valid token, so run `scripts/publish_join_command.sh` |
| Pods `Terminating` for good, or the API not starting after workers changed | A worker that is gone is still a node: see "Removing workers" |
| The certificate stays not ready | `kubectl -n job-applier describe certificate,certificaterequest,order,challenge`; usually the DNS record does not point at `PublicIp` yet |
| A node is `NotReady` | On it: `systemctl status kubelet containerd`, `journalctl -u kubelet -e` |
| The browser shows a 404 page from Traefik | The host you typed is not the chart's `host` |
| The site does not answer at all | The target groups in the EC2 console: an `unhealthy` worker has no Traefik pod (`kubectl -n traefik get pods -o wide`) or its node port is not 30080/30443 (`kubectl -n traefik get svc traefik`) |

## Taking it down

```sh
helm uninstall job-applier -n job-applier             # The app only.
npx cdk destroy Cluster                                # The servers, load balancer, network and IP.
```

Your data is in S3 and DynamoDB, and the images in the `Repositories` stack, none of which this
stack holds.

## Not done yet

- **Removing gone workers:** a cloud controller manager, or a lifecycle hook on the worker
  group, could drain and delete a node when its server goes, instead of you.
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
- **Pod permissions:** every pod on the workers can act as their IAM role. A role per pod needs a
  pod identity webhook and an OIDC issuer.
- **Spending limits:** a user can start any number of tailorings. Create accounts only for people
  you trust.
- **Compiling LaTeX in the API:** a user's edited CV is compiled inside the API pod, with the
  limits in `render.py` but no stronger isolation (see `skipped-work.md`).
