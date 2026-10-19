#!/usr/bin/env bash
# Checks the MHP dev container: tools installed and AWS connected to the dev account.
EXPECTED_ACCOUNT=623096509073

echo "Tools:"
aws --version
terraform version | head -n 1
gh --version | head -n 1
docker --version

echo
echo "AWS identity:"
if ACCOUNT=$(aws sts get-caller-identity --query Account --output text 2>/dev/null); then
  if [ "$ACCOUNT" = "$EXPECTED_ACCOUNT" ]; then
    echo "OK: connected to account $EXPECTED_ACCOUNT"
  else
    echo "WARNING: connected to account $ACCOUNT, expected $EXPECTED_ACCOUNT"
  fi
else
  echo "AWS is not configured yet. In the container terminal, run: aws configure"
  echo "Use region us-west-1. Then run: bash .devcontainer/check-env.sh"
fi

exit 0
