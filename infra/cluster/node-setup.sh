#!/bin/bash
# Prepares an Ubuntu 24.04 server to become a Kubernetes node. It runs once, as EC2 user data,
# when the server is created; the log is in /var/log/cloud-init-output.log. It only installs:
# creating the cluster (kubeadm init and join) is left to you, see docs/kubernetes.md.
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

# 5. Helm, used from the control plane to install the ingress controller, cert-manager and the
#    app's chart.
curl -fsSL https://get.helm.sh/helm-v4.3.1-linux-amd64.tar.gz | tar -xz -C /tmp
install -m 0755 /tmp/linux-amd64/helm /usr/local/bin/helm

echo "node-setup finished" > /var/log/node-setup.done
