# OAuth2 & Forward Authentication Setup

`skt-proxy` supports identity-aware access control for sensitive actions—such as sending torrent tasks directly to a Synology NAS—by inspecting HTTP headers provided by an upstream reverse proxy (e.g., Traefik, Authelia, OAuth2 Proxy, Keycloak, or Cloudflare Access).

---

## How It Works

1. **Authentication Delegation**:
   Your reverse proxy handles user login via OAuth2, OIDC, or SAML.
2. **Header Forwarding**:
   Upon successful authentication, the reverse proxy injects the authenticated user's email into the request header:
   ```http
   X-Forwarded-Email: user@example.com
   ```
3. **Access Enforcement**:
   `skt-proxy` compares the `X-Forwarded-Email` header against the authorized email list defined in the `NAS_ALLOWED_EMAILS` environment variable.
   - If the user's email matches, the **NAS** action button is displayed and enabled.
   - If unauthenticated or unauthorized, NAS actions return HTTP `403 Forbidden`.

---

## Configuration Example

Set the allowed emails in your `.env` file (comma-separated):

```env
NAS_ALLOWED_EMAILS=user1@example.com,user2@domain.cz
```

---

## Reverse Proxy Integration Examples

### 1. OAuth2 Proxy with Nginx

In your Nginx configuration, pass the user email from `oauth2_proxy`:

```nginx
location / {
    auth_request /oauth2/auth;
    auth_request_set $user_email $upstream_http_x_auth_request_email;
    
    proxy_set_header X-Forwarded-Email $user_email;
    proxy_pass http://127.0.0.1:5000;
}
```

### 2. Traefik with ForwardAuth (Authelia / Authentik)

In Traefik dynamic configuration, ensure the `X-Forwarded-Email` header is forwarded:

```yaml
http:
  middlewares:
    my-authelia:
      forwardAuth:
        address: "http://authelia:9091/api/verify?type=plain"
        authResponseHeaders:
          - "Remote-Email"
          - "X-Forwarded-Email"
```

### 3. Cloudflare Access

If using Cloudflare Access, forward the Cloudflare identity email header:

```nginx
proxy_set_header X-Forwarded-Email $http_cf_access_authenticated_user_email;
```
