#!/usr/bin/env bash
# Puts a fresh kubeadm join command in Parameter Store, where infra/cluster/worker.sh reads it to
# join a new worker to the cluster. Run it from your machine with an admin AWS login, after the
# control plane exists (docs/kubernetes.md). The command runs on the control plane through Systems
# Manager, and the join token never reaches your terminal.
set -euo pipefail
ttl="${1:-24h}"  # How long a new worker can join with it; 0 never expires.
# Keep in step with JOIN_PARAMETER in infra/cluster_stack.py.
parameter=/job-applier/cluster/join-command

control_plane=$(aws cloudformation describe-stacks --stack-name Cluster \
    --query "Stacks[0].Outputs[?OutputKey=='ControlPlaneInstanceId'].OutputValue" --output text)
command_id=$(aws ssm send-command --instance-ids "$control_plane" \
    --document-name AWS-RunShellScript --comment "kubeadm join command" \
    --parameters "commands=[\"kubeadm token create --ttl $ttl --print-join-command\"]" \
    --query Command.CommandId --output text)
aws ssm wait command-executed --command-id "$command_id" --instance-id "$control_plane"
join=$(aws ssm get-command-invocation --command-id "$command_id" --instance-id "$control_plane" \
    --query StandardOutputContent --output text)

aws ssm put-parameter --name "$parameter" --type SecureString --overwrite \
    --value "$(echo "$join" | tr -d '\r\n')" >/dev/null
echo "Published a join command valid for $ttl to $parameter."
