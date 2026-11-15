#!/usr/bin/env bash
set -euo pipefail
k=${KUBECTL:-/usr/bin/kubectl}
"$k" -n vllm scale deployment/strata-dsv41-trial --replicas=0
"$k" -n vllm wait --for=delete pod -l app.kubernetes.io/name=strata-dsv41-trial --timeout=180s
"$k" -n vllm scale deployment/strata-orca-trial --replicas=1
"$k" -n vllm rollout status deployment/strata-orca-trial --timeout=45m
"$k" -n vllm patch service strata-orca-trial --type=merge \
  -p '{"spec":{"selector":{"app.kubernetes.io/name":"strata-orca-trial"}}}'
