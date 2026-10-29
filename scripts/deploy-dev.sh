#!/usr/bin/env bash
# Deploy the app (api, web and vault-worker) to dev.
#
#   scripts/deploy-dev.sh <api-tag> <web-tag>              build, push, plan, ask, apply, check
#   scripts/deploy-dev.sh <api-tag> <web-tag> --plan-only  check and plan only; changes nothing
#
# Tags are permanent in ECR. A tag that's already there is reused as is (not
# rebuilt): that's how an unchanged web image keeps its tag. Bump the tag of
# anything you changed.
#
# Steps: checks (tools, AWS account, committed work, tags) -> build and push new
# tags -> terraform plan for terraform/app -> your yes -> apply that exact plan ->
# wait for ECS -> health checks (services, migrations, log errors, pages).
#
# Only the app stack. The other stacks (bootstrap, network, dns, cicd, identity,
# vault, audit) are applied by hand; see README.md.
set -euo pipefail

ACCOUNT=623096509073
REGION=us-west-2
CLUSTER=mhp-dev
SERVICES=(api web vault-worker)
API_REPO=mhp-dev-api
WEB_REPO=mhp-dev-web
REGISTRY="$ACCOUNT.dkr.ecr.$REGION.amazonaws.com"

cd "$(dirname "$0")/.."
ROOT=$(pwd)
PLAN="$ROOT/terraform/app/deploy-dev.tfplan" # *.tfplan is git-ignored
trap 'rm -f "$PLAN"' EXIT

say() { printf '\n== %s\n' "$*"; }
fail() { printf '\nSTOPPED: %s\n' "$*" >&2; exit 1; }

# --- Arguments -----------------------------------------------------------------
[ $# -ge 2 ] || fail "usage: scripts/deploy-dev.sh <api-tag> <web-tag> [--plan-only]"
API_TAG=$1 WEB_TAG=$2 PLAN_ONLY=false
[ "${3:-}" = "--plan-only" ] && PLAN_ONLY=true
for tag in "$API_TAG" "$WEB_TAG"; do
  [[ $tag =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || fail "tag '$tag' isn't like 1.2.3"
done

# --- Checks --------------------------------------------------------------------
say "Checks"
for tool in docker aws terraform jq curl git; do
  command -v "$tool" >/dev/null || fail "$tool isn't installed"
done
[ "$(aws sts get-caller-identity --query Account --output text)" = "$ACCOUNT" ] \
  || fail "AWS credentials aren't for account $ACCOUNT"
# git's own warnings (e.g. about safe.directory entries) are hidden, but if git
# can't read the repo at all, that stops the deploy rather than passing as clean.
changes=$(git status --porcelain 2>/dev/null) || fail "git can't read this repository"
if [ -n "$changes" ]; then
  echo "$changes"
  fail "uncommitted changes. Commit first, so the images match a commit."
fi
COMMIT=$(git rev-parse --short HEAD 2>/dev/null)
echo "commit $COMMIT: $(git log -1 --format=%s 2>/dev/null)"

exists() { aws ecr describe-images --repository-name "$1" --image-ids "imageTag=$2" >/dev/null 2>&1; }
BUILD=()
for pair in "api:$API_REPO:$API_TAG" "web:$WEB_REPO:$WEB_TAG"; do
  IFS=: read -r name repo tag <<<"$pair"
  if exists "$repo" "$tag"; then
    echo "$name $tag: already in ECR, reused as is"
  else
    echo "$name $tag: new, will be built from $COMMIT"
    BUILD+=("$name:$repo:$tag")
  fi
done
if $PLAN_ONLY; then
  [ ${#BUILD[@]} -eq 0 ] || fail "--plan-only needs tags that are already in ECR"
fi

# --- Build and push --------------------------------------------------------------
if [ ${#BUILD[@]} -gt 0 ]; then
  say "Build and push"
  aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$REGISTRY" >/dev/null
  for item in "${BUILD[@]}"; do
    IFS=: read -r name repo tag <<<"$item"
    image="$REGISTRY/$repo:$tag"
    echo "building $name $tag"
    docker build -q --label "org.opencontainers.image.revision=$COMMIT" -t "$image" "app/$name" >/dev/null
    docker push -q "$image" >/dev/null
    echo "pushed $image"
  done
fi

# --- Plan ----------------------------------------------------------------------------
say "Plan (terraform/app)"
VARS=(-var "api_image_tag=$API_TAG" -var "web_image_tag=$WEB_TAG")
if $PLAN_ONLY; then
  terraform -chdir=terraform/app plan -lock=false -input=false -no-color "${VARS[@]}" -out="$PLAN" >/dev/null
else
  terraform -chdir=terraform/app plan -input=false -no-color "${VARS[@]}" -out="$PLAN" >/dev/null
fi
terraform -chdir=terraform/app show -no-color "$PLAN" | grep -E '^  # |^Plan:|^No changes' || true
if $PLAN_ONLY; then
  say "Plan only: nothing was changed"
  exit 0
fi
terraform -chdir=terraform/app show -no-color "$PLAN" | grep -q '^Plan:' || { say "No changes: nothing to deploy"; exit 0; }

read -r -p $'\nApply this plan to dev? Type yes: ' answer
[ "$answer" = "yes" ] || fail "not applied"

# --- Apply and wait ------------------------------------------------------------------
say "Apply"
terraform -chdir=terraform/app apply -input=false -no-color "$PLAN" | grep -E 'Apply complete|Error' || true
STARTED=$(date +%s)

say "Waiting for ECS (up to 15 minutes)"
deadline=$((STARTED + 900))
while aws ecs describe-services --cluster "$CLUSTER" --services "${SERVICES[@]}" \
  --query 'services[].deployments[0].rolloutState' --output text | grep -q IN_PROGRESS; do
  [ "$(date +%s)" -lt "$deadline" ] || fail "rollout still in progress after 15 minutes; check ECS"
  sleep 10
done
aws ecs describe-services --cluster "$CLUSTER" --services "${SERVICES[@]}" \
  --query 'services[].[serviceName,runningCount,desiredCount,deployments[0].rolloutState]' --output text

# --- Health checks ---------------------------------------------------------------------
say "Health checks"
problems=0
since=$(((STARTED - 60) * 1000))
logs() { aws logs filter-log-events --log-group-name "/mhp/dev/$1" --start-time "$since" --query 'events[].message' --output text | tr '\t' '\n'; }

for state in $(aws ecs describe-services --cluster "$CLUSTER" --services "${SERVICES[@]}" \
  --query 'services[].deployments[0].rolloutState' --output text); do
  [ "$state" = "COMPLETED" ] || { echo "rollout state $state"; problems=$((problems + 1)); }
done
logs api | grep -E 'Running upgrade' || echo "no new database migrations"
for group in api web vault-worker; do
  count=$(logs "$group" | grep -c -i -E 'error|traceback' || true)
  echo "$group log errors since deploy: $count"
  [ "$count" -eq 0 ] || problems=$((problems + 1))
done

URL=$(terraform -chdir=terraform/app output -raw url)
for check in "/:200" "/vault:200" "/shared:200" "/api/vault/documents:401"; do
  path=${check%%:*} want=${check##*:}
  got=$(curl -s -m 15 -o /dev/null -w '%{http_code}' "$URL$path")
  echo "$path -> $got (want $want)"
  [ "$got" = "$want" ] || problems=$((problems + 1))
done

say "Done: api $API_TAG, web $WEB_TAG, commit $COMMIT"
[ "$problems" -eq 0 ] || fail "$problems health check(s) failed; see above"
echo "All checks passed."
