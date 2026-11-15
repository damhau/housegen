#!/usr/bin/env bash
# The OAuth apps housegen signs in with (#80), and one env file per environment for the `housegen-auth` secret.
#
#   deploy/auth_apps.sh
#
# Microsoft: an Entra app registration for work and personal accounts, created with az (Graph) in the tenant you are
# logged in to; a rerun keeps the app and adds a secret. Google: the console has no API for web OAuth clients (only
# for IAP, being shut down), so the script names the two pages to fill in and reads what you paste; leave the client
# id empty to skip Google.
set -euo pipefail

NAME=${APP_NAME:-housegen}
OUT=${OUT_DIR:-$HOME/.config/housegen}
GRAPH=https://graph.microsoft.com/v1.0
declare -A URL=([dev]=https://housegen-dev.apps.dhconsulting.ch [prod]=https://housegen.apps.dhconsulting.ch [local]=http://localhost:5173)
declare -A NS=([dev]=housegen-dev [prod]=housegen)
ENVS=(dev prod local)

callbacks() { for e in "${ENVS[@]}"; do printf '%s/auth/callback/%s\n' "${URL[$e]}" "$1"; done; }

graph() {
    local method=$1 path=$2
    shift 2
    az rest --method "$method" --url "$GRAPH$path" --headers Content-Type=application/json "$@"
}

microsoft() {
    az account show >/dev/null 2>&1 || az login --allow-no-subscriptions >/dev/null
    local tenant user tname
    tenant=$(az account show --query tenantId -o tsv)
    user=$(az account show --query user.name -o tsv)
    tname=$(graph get /organization --query 'value[0].displayName' -o tsv)
    echo "Microsoft: the app goes into tenant \"$tname\" ($tenant), signed in as $user."
    # a consultant's az is often logged in to a client's tenant: never register housegen there
    read -r -p "Is this your own tenant? [y/N] " ok
    if [ "$ok" != y ]; then
        echo "Log in to yours first: az login --tenant <tenant id or domain> --allow-no-subscriptions"
        exit 1
    fi

    local redirects id claims
    redirects=$(callbacks microsoft | jq -R . | jq -s .)
    # email: work accounts send none without it; xms_edov: whether the tenant verified the email's
    # domain, the only way a work account's email counts for AUTH_ADMIN_EMAIL (#104)
    claims='{"idToken": [{"name": "email", "essential": false}, {"name": "xms_edov", "essential": false}]}'
    id=$(graph get "/applications?\$filter=displayName%20eq%20'$NAME'" --query 'value[0].id' -o tsv)
    if [ -z "$id" ]; then
        # personal accounts need v2 access tokens set at creation, or Graph refuses the audience
        id=$(graph post /applications --query id -o tsv --body "$(jq -n --arg n "$NAME" --argjson r "$redirects" \
            --argjson c "$claims" \
            '{displayName: $n, signInAudience: "AzureADandPersonalMicrosoftAccount",
              api: {requestedAccessTokenVersion: 2}, web: {redirectUris: $r}, optionalClaims: $c}')")
        echo "  created app registration $NAME"
    else
        local merged
        merged=$(graph get "/applications/$id" --query web.redirectUris -o json | jq --argjson r "$redirects" '. + $r | unique')
        graph patch "/applications/$id" --body "$(jq -n --argjson r "$merged" --argjson c "$claims" \
            '{web: {redirectUris: $r}, optionalClaims: $c}')" >/dev/null
        echo "  app registration $NAME exists: redirect URIs and optional claims updated"
    fi
    MS_ID=$(graph get "/applications/$id" --query appId -o tsv)
    local body expires
    expires=$(date -u -d '+2 years' +%Y-%m-%dT%H:%M:%SZ)
    body=$(jq -n --arg n "$NAME $(date -u +%F)" --arg e "$expires" '{passwordCredential: {displayName: $n, endDateTime: $e}}')
    # a new registration can take a few seconds to reach every Graph replica
    for _ in 1 2 3 4 5; do
        MS_SECRET=$(graph post "/applications/$id/addPassword" --body "$body" --query secretText -o tsv 2>/dev/null) && break
        sleep 5
    done
    [ -n "${MS_SECRET:-}" ] || { echo "addPassword failed for $id"; exit 1; }
    echo "  client id $MS_ID, new secret valid until ${expires%T*}"
}

google() {
    echo
    echo "Google: two console pages."
    echo "  1. https://console.cloud.google.com/auth/branding"
    echo "     Pick or create a project ($NAME). App name $NAME, your support email, audience External."
    echo "     Then Audience > Publish app: openid, email and profile need no verification; in Testing only"
    echo "     listed test users can sign in."
    echo "  2. https://console.cloud.google.com/auth/clients > Create client > Web application, name $NAME,"
    echo "     Authorized redirect URIs:"
    callbacks google | sed 's/^/       /'
    read -r -p "Client ID (empty to skip Google): " G_ID
    G_SECRET=
    if [ -n "$G_ID" ]; then
        read -r -s -p "Client secret: " G_SECRET
        echo
    fi
}

write_envs() {
    local admin
    read -r -p "Admin email (owner of the organisation the existing projects move to): " admin
    mkdir -p "$OUT"
    chmod 700 "$OUT"
    for e in "${ENVS[@]}"; do
        local f="$OUT/auth-$e.env"
        (
            umask 077
            {
                echo "AUTH_MICROSOFT_CLIENT_ID=$MS_ID"
                echo "AUTH_MICROSOFT_CLIENT_SECRET=$MS_SECRET"
                if [ -n "$G_ID" ]; then
                    echo "AUTH_GOOGLE_CLIENT_ID=$G_ID"
                    echo "AUTH_GOOGLE_CLIENT_SECRET=$G_SECRET"
                fi
                echo "AUTH_SESSION_SECRET=$(openssl rand -hex 32)"
                echo "AUTH_ADMIN_EMAIL=$admin"
            } >"$f"
        )
        echo "  $f"
    done
}

echo "Callback URLs:"
callbacks microsoft
callbacks google
echo
microsoft
google
echo
echo "Env files (one session secret per environment):"
write_envs
echo
echo "Next, with your own kubeconfig (an admin one, not the read-only claude-code.yaml):"
for e in dev prod; do
    echo "  kubectl -n ${NS[$e]} create secret generic housegen-auth --from-env-file=$OUT/auth-$e.env"
done
echo "and append $OUT/auth-local.env to backend/.env to sign in locally (otherwise run with AUTH_DISABLED=1)."
