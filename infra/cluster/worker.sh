#!/bin/bash
# Prepares an Ubuntu 24.04 server to be a Kubernetes worker and joins it to the cluster. It runs
# once, as EC2 user data, when the server is created; the log is in /var/log/cloud-init-output.log.
# Steps 1-4 are the same as in control-plane.sh: keep them in step. The join command comes from
# Parameter Store, put there by scripts/publish_join_command.sh, see docs/kubernetes.md. The
# worker is one of the Cluster stack's Auto Scaling group, which starts and terminates them.
set -euxo pipefail

KUBERNETES_MINOR=v1.37
ECR_PROVIDER_VERSION=v1.37.0

# 1. Kernel settings. Pods talk through a Linux bridge and the node routes between them, so
#    bridged traffic must pass through iptables and the node must forward packets.
cat > /etc/modules-load.d/kubernetes.conf <<'CONF'
overlay
br_netfilter
CONF
modprobe overlay
modprobe br_netfilter
cat > /etc/sysctl.d/99-kubernetes.conf <<'CONF'
net.bridge.bridge-nf-call-iptables  = 1
net.bridge.bridge-nf-call-ip6tables = 1
net.ipv4.ip_forward                 = 1
CONF
sysctl --system

# The kubelet refuses to run with swap on. EC2 Ubuntu images have none, but be sure.
swapoff -a

# 2. The container runtime: containerd 2 from Docker's repository, as Ubuntu's own is 1.7,
#    which Kubernetes no longer supports.
apt-get update
apt-get install -y ca-certificates curl gpg apt-transport-https unzip
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
echo "deb [signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu \
$(. /etc/os-release && echo "$VERSION_CODENAME") stable" > /etc/apt/sources.list.d/docker.list
apt-get update
apt-get install -y containerd.io
# Docker's package turns off the CRI plugin that Kubernetes talks to; the default config has
# it on. Kubernetes and containerd must agree on who manages cgroups: systemd, on Ubuntu.
containerd config default > /etc/containerd/config.toml
sed -i 's/SystemdCgroup = false/SystemdCgroup = true/' /etc/containerd/config.toml
grep -q 'SystemdCgroup = true' /etc/containerd/config.toml
systemctl restart containerd
systemctl enable containerd

# 3. kubeadm, kubelet and kubectl, held so that apt upgrade never moves the cluster to another
#    version: upgrading Kubernetes is its own procedure.
curl -fsSL "https://pkgs.k8s.io/core:/stable:/${KUBERNETES_MINOR}/deb/Release.key" \
  | gpg --dearmor -o /etc/apt/keyrings/kubernetes.gpg
echo "deb [signed-by=/etc/apt/keyrings/kubernetes.gpg] \
https://pkgs.k8s.io/core:/stable:/${KUBERNETES_MINOR}/deb/ /" > /etc/apt/sources.list.d/kubernetes.list
apt-get update
apt-get install -y kubelet kubeadm kubectl
apt-mark hold kubelet kubeadm kubectl

# 4. Pulling images from ECR. There is no docker login here: the kubelet asks this plugin for a
#    password whenever an image is on ECR, and the plugin gets one with the server's IAM role.
mkdir -p /etc/kubernetes/credential-providers
curl -fsSL -o /etc/kubernetes/credential-providers/ecr-credential-provider \
  "https://artifacts.k8s.io/binaries/cloud-provider-aws/${ECR_PROVIDER_VERSION}/linux/amd64/ecr-credential-provider-linux-amd64"
chmod 0755 /etc/kubernetes/credential-providers/ecr-credential-provider
cat > /etc/kubernetes/credential-provider-config.yaml <<'CONF'
apiVersion: kubelet.config.k8s.io/v1
kind: CredentialProviderConfig
providers:
  - name: ecr-credential-provider
    matchImages:
      - "*.dkr.ecr.*.amazonaws.com"
    defaultCacheDuration: "12h"
    apiVersion: credentialprovider.kubelet.k8s.io/v1
CONF
cat > /etc/default/kubelet <<'CONF'
KUBELET_EXTRA_ARGS=--image-credential-provider-config=/etc/kubernetes/credential-provider-config.yaml --image-credential-provider-bin-dir=/etc/kubernetes/credential-providers
CONF
systemctl enable kubelet

echo "node-setup finished" > /var/log/node-setup.done

# 5. The AWS CLI, to read the join command. Ubuntu's image does not have it.
curl -fsSL -o /tmp/awscliv2.zip https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip
unzip -q /tmp/awscliv2.zip -d /tmp
/tmp/aws/install

metadata=http://169.254.169.254/latest
token=$(curl -fsS -X PUT "$metadata/api/token" -H "X-aws-ec2-metadata-token-ttl-seconds: 300")
region=$(curl -fsS -H "X-aws-ec2-metadata-token: $token" "$metadata/meta-data/placement/region")
instance=$(curl -fsS -H "X-aws-ec2-metadata-token: $token" "$metadata/meta-data/instance-id")

# 6. Calico routes pod traffic between nodes without wrapping it, so packets carry pod addresses,
#    which EC2 drops unless the source/dest check is off. The control plane has it off in the
#    stack; a launch template cannot do that, so each worker does it here. On a new stack the
#    role's permissions can arrive a little after the server, hence the retries.
until aws ec2 modify-instance-attribute --region "$region" --instance-id "$instance" \
    --no-source-dest-check; do
  sleep 10
done

# 7. Join the cluster. The parameter may not be there yet, or hold an expired token, so keep
#    trying until it works: publishing a new one is enough, nothing needs to be run here.
#    Keep the name in step with JOIN_PARAMETER in infra/cluster_stack.py.
JOIN_PARAMETER=/job-applier/cluster/join-command
set +x  # The join command holds the cluster's join token: keep it out of the log.
until [ -f /etc/kubernetes/kubelet.conf ]; do
  join=$(aws ssm get-parameter --region "$region" --name "$JOIN_PARAMETER" --with-decryption \
    --query Parameter.Value --output text 2>/dev/null || true)
  if [[ "$join" == "kubeadm join "* ]] && $join; then
    break
  fi
  # A join that failed half way leaves files behind that stop the next one.
  kubeadm reset -f >/dev/null 2>&1 || true
  echo "Could not join yet (no join command, or it has expired); trying again in a minute."
  sleep 60
done
echo "joined the cluster" > /var/log/node-join.done
