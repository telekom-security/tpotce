# Security Policy

## Supported Versions

| Version | Supported          |
|-------|--------------------|
| 24.04 | :white_check_mark: |


## Reporting a Vulnerability

We prioritize the security of T-Pot highly. Often, vulnerabilities in T-Pot components stem from upstream dependencies, including honeypots, Docker images, tools, or packages. We are committed to working together to resolve any issues effectively.

Please follow these steps before reporting a potential vulnerability:

1. Verify that the behavior you've observed isn't already documented as a normal aspect or unrelated issue of T-Pot. For example, Cowrie may initiate outgoing connections, or T-Pot might open all possible TCP ports—a feature enabled by Honeytrap.
2. Clearly identify which component is vulnerable (e.g., a specific honeypot, Docker image, tool, package) and isolate the issue.
3. Provide a detailed description of the issue, including log and, if available, debug files. Include all steps necessary to reproduce the vulnerability. If you have a proposed solution, hotfix, or patch, please be prepared to submit a pull request (PR).
4. Check whether the vulnerability is already known upstream. If there is an existing fix or patch, include that information in your report.

This approach ensures a thorough and efficient resolution process.

We aim to respond as quickly as possible. If you believe the issue poses an immediate threat to the entire T-Pot community, you can expedite the process by responsibly alerting our [CERT](https://www.telekom.com/en/corporate-responsibility/data-protection-data-security/security/details/introducing-deutsche-telekom-cert-358316).
