# Security policy

Version 0.3.0 permits opt-in password access without local approval. Protect this credential as remote-control authority. No password is configured by default; clipboard and file permissions default off for password sessions. The first connection requires out-of-band comparison of the displayed certificate fingerprint. Later connections reject a changed identity. Settings, address book contacts, and private keys use current-user Windows DPAPI; passwords use salted scrypt verifiers. This does not protect against malware running as the same Windows user.

Incoming files (and auto-zipped folders) are stored in locally generated folders, verified by SHA-256, marked as network-origin attachments, and never automatically executed. Clipboard access is user-triggered. File transfer cancellation aborts only the active/queued file transfer and cleans up `.partial` files without dropping the session. Host users can toggle control, clipboard, and file permissions in real time during an active session. Audit logs are local and rotated, not tamper-resistant. See `LanDesk/README.md` for the tested scope and limitations.

LanDesk is currently an internal pilot. Do not expose its listening port to the public internet or configure router port forwarding. Restrict inbound access with the organization's firewall to approved support networks on Domain or Private profiles.

Security reports must be sent privately to the repository owner or the organization's security team. Include the affected version, impact, reproduction steps, and any relevant sanitized logs. Do not include active passwords, temporary session codes, private keys, screen captures, or personal data.

Before production use, the organization should require code review, signed releases, dependency and malware scanning, centralized identity and authorization, tamper-resistant audit logging, incident response procedures, and penetration testing.
