# Security and privacy

This is a single-user, self-hosted integration. There is no hosted service.
Do not post real tokens, health exports, logs containing personal information, or provisioning profiles in issues.

- Use your own HTTPS domain and a trusted certificate. The iOS app rejects non-HTTPS endpoints and does not follow redirects for uploads.
- The receiver uses one Bearer token to authorize all writes. Keep it secret; rotate it if disclosed, then update the iOS app.
- Health records are unencrypted JSON on your disk. Restrict filesystem access, encrypt backups, and protect the AstrBot dashboard separately.
- The public health receiver has no data-read API. Health history is available through authenticated AstrBot WebUI routes and LLM tools; restrict bot/plugin access accordingly.
- Wake events can initiate LLM calls and messages to the administrator-configured session. They are opt-in, authenticated, date-deduplicated, and require a recent upload. A compromised token can still fabricate records or trigger an authorized daily wake event.
- A successful upload does not mean an AI message was delivered. Queueing, model availability, bot permissions and platform delivery are separate steps.
- This is not medical software. Measurements, especially individual blood-oxygen readings and wrist temperature, must not be used by the agent to diagnose disease.

For a suspected vulnerability, use GitHub's private vulnerability reporting when enabled; otherwise contact the maintainer without including secrets in public reports.
