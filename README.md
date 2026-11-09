# LanDesk Intranet Remote Desktop

LanDesk 0.3.0 is an AnyDesk-inspired Windows intranet remote desktop pilot. Connect by IP or from the built-in Address Book with explicit consent, or opt in to password access at the receiving computer. It includes TLS 1.2+, persistent certificate pinning, scrypt password hashes, Windows DPAPI, multi-monitor switching, in-session chat, live permission toggles, auto-ZIP folder transfer, one-step auto-paste, and non-destructive file transfer cancellation.

## Download and run

The current local release is under `outputs/v0.3.0/`: download/copy `LanDesk-v0.3.0-Windows-x64.zip`, verify against that version's `SHA256SUMS.txt`, extract the whole folder, and run `LanDesk.exe` on both computers.

For Thai setup, firewall, security, limitations, and usage instructions, see [`LanDesk/README.md`](LanDesk/README.md).

## Security status

This is a controlled IT pilot, not approved for broad production deployment. Without a configured password the receiver must approve each session. Configuring a password explicitly enables access without a local approval prompt, while the application is open and listening. Before wider use, add organization identity controls such as AD/SSO and MFA, centralized tamper-resistant audit logs, signed binaries, dependency scanning, code review, and a penetration test.

Report suspected vulnerabilities privately to the repository owner. Do not publish credentials, session codes, fingerprints, logs, screenshots, or employee information in GitHub issues.
