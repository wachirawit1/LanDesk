# LanDesk Intranet Remote Desktop

LanDesk 0.3.2 is an AnyDesk-inspired Windows intranet remote desktop pilot. Connect by IP or from the built-in Address Book with explicit consent, or opt in to password access at the receiving computer. It includes a single-file Setup Installer (`LanDesk-Setup-v0.3.2.exe`), automatic background Tray service, GitHub auto-update, seamless file clipboard copy-paste, TLS 1.2+, persistent certificate pinning, scrypt password hashes, Windows DPAPI, multi-monitor switching, in-session chat, and live permission toggles.

## Download and run

The current release is under `outputs/v0.3.2/` and `releases/`:
- **Single-file Installer (Recommended):** Run `LanDesk-Setup-v0.3.2.exe` for one-click installation with desktop shortcuts and auto-start background service.
- **Portable ZIP:** Extract `LanDesk-v0.3.2-Windows-x64.zip` and run `LanDesk.exe`.

Verify integrity against `SHA256SUMS.txt`.

For Thai setup, firewall, security, limitations, and usage instructions, see [`LanDesk/README.md`](LanDesk/README.md).

## Security status

This is a controlled IT pilot, not approved for broad production deployment. Without a configured password the receiver must approve each session. Configuring a password explicitly enables access without a local approval prompt, while the application is open and listening. Before wider use, add organization identity controls such as AD/SSO and MFA, centralized tamper-resistant audit logs, signed binaries, dependency scanning, code review, and a penetration test.

Report suspected vulnerabilities privately to the repository owner. Do not publish credentials, session codes, fingerprints, logs, screenshots, or employee information in GitHub issues.
