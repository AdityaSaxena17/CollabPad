#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 deploy|destroy"
}

case "${1:-}" in
  deploy|destroy) action=$1 ;;
  -h|--help) usage; exit 0 ;;
  *) usage >&2; exit 2 ;;
esac
if [[ $# -ne 1 ]]; then usage >&2; exit 2; fi

cd "$(dirname "${BASH_SOURCE[0]}")/../.."
region=ap-south-1
stack=collabpad
parameter=/collabpad/app-env
hostname=collabpad.mukthish.co.in
aws_cli=(aws --region "$region" --no-cli-pager)

stack_output() {
  "${aws_cli[@]}" cloudformation describe-stacks --stack-name "$stack" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue | [0]" --output text
}

destroy() {
  local stack_id lookup bucket parameter_name
  if lookup=$("${aws_cli[@]}" cloudformation describe-stacks --stack-name "$stack" \
      --query 'Stacks[0].StackId' --output text 2>&1); then
    stack_id=$lookup
    bucket=$("${aws_cli[@]}" cloudformation list-stack-resources --stack-name "$stack_id" \
      --query "StackResourceSummaries[?LogicalResourceId=='ReleaseBucket' && (ResourceStatus=='CREATE_COMPLETE' || ResourceStatus=='UPDATE_COMPLETE' || ResourceStatus=='DELETE_FAILED')].PhysicalResourceId | [0]" --output text)
    if [[ -n "$bucket" && "$bucket" != None ]]; then
      "${aws_cli[@]}" s3api delete-object --bucket "$bucket" --key app.tar.gz
    fi
    "${aws_cli[@]}" cloudformation delete-stack --stack-name "$stack_id"
    "${aws_cli[@]}" cloudformation wait stack-delete-complete --stack-name "$stack_id"
  elif [[ "$lookup" != *"does not exist"* ]]; then
    printf '%s\n' "$lookup" >&2
    return 1
  fi
  parameter_name=$("${aws_cli[@]}" ssm describe-parameters \
    --parameter-filters "Key=Name,Option=Equals,Values=$parameter" \
    --query 'Parameters[0].Name' --output text)
  if [[ "$parameter_name" == "$parameter" ]]; then
    "${aws_cli[@]}" ssm delete-parameter --name "$parameter"
  fi
  echo "AWS deployment removed. Remove the collabpad A record in GoDaddy."
}

deploy() (
  umask 077
  temp= instance= bucket= ip= existing= state= command=
  temp=$(mktemp -d)
  trap 'rm -rf -- "${temp:?}"' EXIT

  node - "$hostname" > "$temp/runtime.env" <<'JS'
require("@next/env").loadEnvConfig(process.cwd());
const { randomBytes } = require("node:crypto");
for (const key of ["NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY", "CLERK_SECRET_KEY", "CLERK_WEBHOOK_SIGNING_SECRET"]) {
  const value = process.env[key] || "";
  if (!/^[A-Za-z0-9_+=/.-]*$/.test(value) || (!value && key !== "CLERK_WEBHOOK_SIGNING_SECRET")) {
    throw new Error("Missing or invalid " + key);
  }
  console.log(key + "=" + value);
}
const password = randomBytes(24).toString("hex");
console.log("COLLABPAD_HOSTNAME=" + process.argv[2]);
console.log("POSTGRES_PASSWORD=" + password);
console.log("AWS_WEB_DATABASE_URL=postgresql://collab_docs:" + password + "@postgres:5432/collab_docs");
JS
  # Include pending application changes, but omit local credentials and artifacts.
  git ls-files -z --cached --others --exclude-standard -- \
    .dockerignore package.json package-lock.json tsconfig.json postcss.config.mjs \
    next.config.ts proxy.ts app components lib public db services deploy/aws \
    docker-compose.yml docker-compose.raft.yml docker-compose.bedrock.yml docker-compose.aws.yml |
    python3 -c '
import sys, tarfile
from pathlib import Path

paths = set(sys.stdin.buffer.read().split(b"\0")) - {b""}
with tarfile.open(sys.argv[1], "w:gz") as archive:
    for name in sorted(paths):
        path = Path(name.decode())
        if path.is_file() and not path.is_symlink() and not any(part.startswith(".env") for part in path.parts):
            archive.add(path, arcname=str(path))
' "$temp/app.tar.gz"

  "${aws_cli[@]}" cloudformation deploy --stack-name "$stack" \
    --template-file deploy/aws/infrastructure.yml --capabilities CAPABILITY_IAM \
    --no-fail-on-empty-changeset
  instance=$(stack_output InstanceId)
  bucket=$(stack_output ReleaseBucketName)
  ip=$(stack_output PublicIp)
  printf 'GoDaddy DNS: set collabpad A to %s\n' "$ip"

  "${aws_cli[@]}" s3 cp "$temp/app.tar.gz" "s3://$bucket/app.tar.gz" --only-show-errors
  existing=$("${aws_cli[@]}" ssm describe-parameters \
    --parameter-filters "Key=Name,Option=Equals,Values=$parameter" \
    --query 'Parameters[0].Name' --output text)
  if [[ "$existing" != "$parameter" ]]; then
    "${aws_cli[@]}" ssm put-parameter --name "$parameter" --type SecureString \
      --value "file://$temp/runtime.env" >/dev/null
  fi
  rm -f "$temp/runtime.env"

  for _ in {1..60}; do
    state=$("${aws_cli[@]}" ssm describe-instance-information \
      --filters "Key=InstanceIds,Values=$instance" \
      --query 'InstanceInformationList[0].PingStatus' --output text)
    [[ "$state" != Online ]] || break
    sleep 5
  done
  if [[ "$state" != Online ]]; then echo "EC2 did not join Systems Manager." >&2; return 1; fi

  python3 - "$bucket" "$region" "$parameter" > "$temp/command.json" <<'PY'
import json
import shlex
import sys

bucket, region, parameter = map(shlex.quote, sys.argv[1:])
compose = "docker compose -f docker-compose.yml -f docker-compose.raft.yml -f docker-compose.bedrock.yml -f docker-compose.aws.yml"
print(json.dumps({"executionTimeout": ["1200"], "commands": [
    "set -eu",
    "cloud-init status --wait",
    "cd /opt/collabpad",
    "umask 022",
    "release=$(mktemp)",
    "trap 'rm -f \"$release\"' EXIT",
    f'aws s3 cp s3://{bucket}/app.tar.gz "$release" --region {region} --only-show-errors',
    'tar -xzf "$release" --no-same-owner',
    "chmod 0755 db/migrations",
    "(umask 077; "
    f"aws ssm get-parameter --region {region} --name {parameter} --with-decryption "
    "--query Parameter.Value --output text > .env)",
    "chmod 0600 .env",
    f"{compose} config --quiet",
    f"{compose} up --build -d",
    f"{compose} ps -a",
]}))
PY
  command=$("${aws_cli[@]}" ssm send-command --document-name AWS-RunShellScript \
    --instance-ids "$instance" --parameters "file://$temp/command.json" \
    --query Command.CommandId --output text)
  printf 'Building the app on %s (SSM command %s)\n' "$instance" "$command"
  for _ in {1..240}; do
    state=$("${aws_cli[@]}" ssm get-command-invocation --command-id "$command" \
      --instance-id "$instance" --query Status --output text 2>/dev/null || true)
    case "$state" in Success|Failed|TimedOut|Cancelled|Cancelling) break ;; esac
    sleep 5
  done
  if [[ "$state" != Success ]]; then
    "${aws_cli[@]}" ssm get-command-invocation --command-id "$command" --instance-id "$instance" \
      --query '{Status:Status,Error:StandardErrorContent}' --output json >&2
    return 1
  fi
  printf 'Deployed https://%s. HTTPS becomes available after DNS propagates.\n' "$hostname"
)

"${aws_cli[@]}" sts get-caller-identity >/dev/null
"$action"
